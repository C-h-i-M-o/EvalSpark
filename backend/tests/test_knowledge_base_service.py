import asyncio
import io
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.engine import make_url
from starlette.datastructures import Headers, UploadFile

from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, RagJob
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBasePatch
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag.errors import KnowledgeBaseError


def upload() -> UploadFile:
    return UploadFile(io.BytesIO("公司制度".encode()), filename="制度.txt", headers=Headers({"content-type": "text/plain"}))


@pytest.mark.asyncio
async def test_other_user_and_admin_cannot_read_update_or_download(rag_sessions, rag_users, tmp_path: Path) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner, other = rag_users
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="私有库"))
        accepted = await service.upload(db, owner, kb.id, upload())
        for action in [
            lambda: service.get(db, other, kb.id),
            lambda: service.patch(db, other, kb.id, KnowledgeBasePatch(name="越权")),
            lambda: service.download(db, other, kb.id, accepted.document_id),
            lambda: service.delete_document(db, other, kb.id, accepted.document_id),
        ]:
            with pytest.raises(KnowledgeBaseError) as error:
                await action()
            assert error.value.status_code == 404
        listed = await service.list(db, other, 1, 10)
        assert listed.total == 0


@pytest.mark.asyncio
async def test_publish_happens_after_commit_and_failure_keeps_document(rag_sessions, rag_users, tmp_path: Path) -> None:
    owner = rag_users[0]
    published: list[str] = []
    committed_when_published: list[int] = []
    def fail_publish(job_id: str) -> bool:
        # 独立连接只能读到已经提交的作业，直接验证先提交后投递的顺序。
        engine = create_engine(make_url(os.environ["DATABASE_URL"]).set(drivername="mysql+pymysql"))
        try:
            with engine.connect() as connection:
                committed_when_published.append(connection.scalar(text("SELECT COUNT(*) FROM rag_jobs WHERE id=:id"), {"id": job_id}))
        finally:
            engine.dispose()
        published.append(job_id)
        raise ConnectionError("不能回显的 broker 地址")
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=fail_publish)
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="队列测试"))
        accepted = await service.upload(db, owner, kb.id, upload())
        assert accepted.status == "queued" and accepted.dispatch_pending
    async with rag_sessions() as db:
        job = await db.get(RagJob, accepted.job_id)
        document = await db.get(KnowledgeDocument, accepted.document_id)
        assert job is not None and job.status == "queued"
        assert document is not None and (tmp_path / document.storage_key).is_file()
        assert job.id in published
        assert committed_when_published == [1]


@pytest.mark.asyncio
async def test_delete_is_idempotent_and_immediately_blocks_download(rag_sessions, rag_users, tmp_path: Path) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="删除测试"))
        uploaded = await service.upload(db, owner, kb.id, upload())
        first = await service.delete_document(db, owner, kb.id, uploaded.document_id)
        second = await service.delete_document(db, owner, kb.id, uploaded.document_id)
        assert first.job_id == second.job_id
        assert first.status == "queued"
        with pytest.raises(KnowledgeBaseError) as error:
            await service.download(db, owner, kb.id, uploaded.document_id)
        assert error.value.status_code == 404
        assert len(list(tmp_path.iterdir())) == 1  # 异步清理尚未执行，不能伪报物理删除。


@pytest.mark.asyncio
async def test_mysql_row_lock_keeps_concurrent_uploads_within_one_hundred_documents(
    rag_sessions, rag_users, tmp_path: Path,
) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="并发配额"))
        for index in range(99):
            db.add(KnowledgeDocument(
                knowledge_base_id=kb.id, user_id=owner, original_name="占位.txt",
                storage_key=f"quota-{kb.id}-{index}", media_type="text/plain", size_bytes=1,
                content_hash="0" * 64, status="failed", index_revision=1, chunk_count=0,
            ))
        await db.commit()
    async def attempt() -> str:
        async with rag_sessions() as db:
            try:
                await service.upload(db, owner, kb.id, upload())
                return "accepted"
            except KnowledgeBaseError as error:
                return error.code
    assert sorted(await asyncio.gather(attempt(), attempt())) == ["accepted", "document_limit"]
    async with rag_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == kb.id,
        )) == 100
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_patch_checks_merged_chunking_and_preserves_omitted_fields(rag_sessions, rag_users, tmp_path: Path) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="原名", chunkSize=400, chunkOverlap=300))
        with pytest.raises(KnowledgeBaseError) as error:
            await service.patch(db, owner, kb.id, KnowledgeBasePatch(chunkSize=256))
        assert error.value.status_code == 422
        updated = await service.patch(db, owner, kb.id, KnowledgeBasePatch(description="说明"))
        assert updated.name == "原名" and updated.chunk_size == 400 and updated.chunk_overlap == 300


@pytest.mark.asyncio
async def test_reindex_versions_and_delete_preserve_invalid_chunking(rag_sessions, rag_users, tmp_path: Path) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="重建验证"))
        accepted = await service.upload(db, owner, kb.id, upload())
        document = await db.get(KnowledgeDocument, accepted.document_id)
        job = await db.get(RagJob, accepted.job_id)
        row = await db.get(KnowledgeBase, kb.id)
        document.status, document.chunk_count, job.status, row.status = "ready", 1, "succeeded", "ready"
        await db.commit()
        changed = await service.patch(db, owner, kb.id, KnowledgeBasePatch(chunkSize=1000))
        assert changed.status == "reindex_required" and not changed.available
        with pytest.raises(KnowledgeBaseError):
            await service.upload(db, owner, kb.id, upload())
        assert len(list(tmp_path.iterdir())) == 1
        reindex = await service.reindex(db, owner, kb.id)
        assert (await service.reindex(db, owner, kb.id)).job_id == reindex.job_id
        document = await db.get(KnowledgeDocument, accepted.document_id)
        assert document.index_revision == 2 and document.status == "queued"
        current = await service.list_documents(db, owner, kb.id, 1, 20)
        assert current.items[0].current_job.id == reindex.job_id
        with pytest.raises(KnowledgeBaseError):
            await service.retry(db, owner, kb.id, accepted.document_id)
        # 模拟后台完成重建，再修改参数；删除不能把旧配置的块恢复成可检索。
        document = await db.get(KnowledgeDocument, accepted.document_id)
        job = await db.get(RagJob, reindex.job_id)
        row = await db.get(KnowledgeBase, kb.id)
        document.status, job.status, row.status = "ready", "succeeded", "ready"
        await db.commit()
        await service.patch(db, owner, kb.id, KnowledgeBasePatch(chunkSize=1200))
        deleted = await service.delete_document(db, owner, kb.id, accepted.document_id)
        assert (await service.get(db, owner, kb.id)).status == "reindex_required"
        documents = await service.list_documents(db, owner, kb.id, 1, 20)
        assert documents.items[0].current_job.id == deleted.job_id


@pytest.mark.asyncio
async def test_database_failure_removes_only_uncommitted_upload(rag_sessions, rag_users, tmp_path: Path, monkeypatch) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="回滚验证"))
        committed = await service.upload(db, owner, kb.id, upload())
        before = set(tmp_path.iterdir())
        original_commit = db.commit
        count = 0
        async def failing_commit() -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("测试事务提交失败")
            await original_commit()
        monkeypatch.setattr(db, "commit", failing_commit)
        with pytest.raises(RuntimeError):
            await service.upload(db, owner, kb.id, upload())
        assert set(tmp_path.iterdir()) == before
        assert await db.get(KnowledgeDocument, committed.document_id) is not None
        assert await db.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb.id)) == 1


@pytest.mark.asyncio
async def test_ambiguous_commit_failure_never_removes_committed_file(rag_sessions, rag_users, tmp_path: Path, monkeypatch) -> None:
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="提交确认丢失"))
        original_commit = db.commit
        count = 0
        async def ambiguous_commit() -> None:
            nonlocal count
            count += 1
            await original_commit()
            if count == 2:
                raise ConnectionError("提交成功但连接响应丢失")
        monkeypatch.setattr(db, "commit", ambiguous_commit)
        with pytest.raises(ConnectionError):
            await service.upload(db, owner, kb.id, upload())
    async with rag_sessions() as db:
        document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb.id))
        assert document is not None
        assert (tmp_path / document.storage_key).is_file()


@pytest.mark.asyncio
async def test_mutation_does_not_use_authentication_transaction_old_snapshot(rag_sessions, rag_users, tmp_path: Path) -> None:
    from app.models.user import User
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="快照隔离"))
        await db.scalar(select(User).where(User.id == owner))  # 模拟鉴权开启 REPEATABLE READ 快照。
        async with rag_sessions() as competing:
            await service.upload(competing, owner, kb.id, upload())
        with pytest.raises(KnowledgeBaseError) as error:
            await service.patch(db, owner, kb.id, KnowledgeBasePatch(chunkSize=1000))
        assert error.value.code == "knowledge_base_busy"


@pytest.mark.asyncio
async def test_failed_document_retry_publishes_new_version_once(rag_sessions, rag_users, tmp_path: Path) -> None:
    published: list[str] = []
    def publish(job_id: str) -> bool:
        published.append(job_id)
        return True
    service = KnowledgeBaseService(documents_dir=tmp_path, publisher=publish)
    owner = rag_users[0]
    async with rag_sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="失败重试"))
        uploaded = await service.upload(db, owner, kb.id, upload())
        assert not uploaded.dispatch_pending
        document = await db.get(KnowledgeDocument, uploaded.document_id)
        job = await db.get(RagJob, uploaded.job_id)
        row = await db.get(KnowledgeBase, kb.id)
        document.status, job.status, row.status = "failed", "failed", "empty"
        await db.commit()
        retried = await service.retry(db, owner, kb.id, uploaded.document_id)
        assert retried.job_id != uploaded.job_id and not retried.dispatch_pending
        assert (await service.retry(db, owner, kb.id, uploaded.document_id)).job_id == retried.job_id
        document = await db.get(KnowledgeDocument, uploaded.document_id)
        job = await db.get(RagJob, retried.job_id)
        assert document.index_revision == job.target_revision == 2
        assert job.dispatched_at is not None
        assert published == [uploaded.job_id, retried.job_id]
