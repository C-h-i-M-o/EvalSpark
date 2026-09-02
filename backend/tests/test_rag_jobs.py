import asyncio
import importlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from starlette.datastructures import Headers, UploadFile

from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, RagJob
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.knowledge_base_service import KnowledgeBaseService


def repository(sessions):
    assert importlib.util.find_spec("app.services.rag.jobs") is not None, "尚未实现作业租约"
    return importlib.import_module("app.services.rag.jobs").JobRepository(sessions)


async def queued_document(sessions, owner: int, path: Path):
    service = KnowledgeBaseService(documents_dir=path, publisher=lambda _: False)
    async with sessions() as db:
        kb = await service.create(db, owner, KnowledgeBaseCreate(name="索引租约测试"))
        upload = UploadFile(io.BytesIO("制度正文".encode()), filename="制度.txt", headers=Headers({"content-type": "text/plain"}))
        job = await service.upload(db, owner, kb.id, upload)
    return service, kb.id, job


@pytest.mark.asyncio
async def test_duplicate_claim_is_exclusive_and_other_library_can_run(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    _, _, first = await queued_document(rag_sessions, rag_users[0], tmp_path)
    _, _, second = await queued_document(rag_sessions, rag_users[0], tmp_path)
    claims = await asyncio.gather(repo.claim(first.job_id), repo.claim(first.job_id))
    assert sum(item is not None for item in claims) == 1
    assert await repo.claim(second.job_id) is not None


@pytest.mark.asyncio
async def test_same_library_serializes_delete_after_running_index(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    service, kb_id, job = await queued_document(rag_sessions, rag_users[0], tmp_path)
    claim = await repo.claim(job.job_id)
    async with rag_sessions() as db:
        cleanup = await service.delete_document(db, rag_users[0], kb_id, job.document_id)
    assert await repo.claim(cleanup.job_id) is None
    with pytest.raises(Exception) as error:
        await repo.checkpoint(claim, "embedding", document_id=job.document_id, revision=1)
    assert getattr(error.value, "code", None) == "job_superseded"
    await repo.finish(claim, error_code="job_superseded")
    assert await repo.claim(cleanup.job_id) is not None


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_old_claim_cannot_mutate(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    _, _, job = await queued_document(rag_sessions, rag_users[0], tmp_path)
    first = await repo.claim(job.job_id)
    async with rag_sessions() as db:
        await db.execute(update(RagJob).where(RagJob.id == job.job_id).values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)))
        await db.commit()
    assert job.job_id in await repo.recoverable(limit=10_000)
    second = await repo.claim(job.job_id)
    assert second.attempt == first.attempt + 1
    with pytest.raises(Exception) as error:
        await repo.checkpoint(first, "indexing")
    assert getattr(error.value, "code", None) == "job_lease_lost"
    await repo.finish(first, error_code="old_worker_failure")
    async with rag_sessions() as db:
        current = await db.get(RagJob, job.job_id)
        assert current.status == "running" and current.error_code is None


@pytest.mark.asyncio
async def test_recovery_includes_lost_dispatched_messages_but_not_live_jobs(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    _, _, pending = await queued_document(rag_sessions, rag_users[0], tmp_path)
    _, _, running = await queued_document(rag_sessions, rag_users[0], tmp_path)
    await repo.claim(running.job_id)
    async with rag_sessions() as db:
        await db.execute(update(RagJob).where(RagJob.id == pending.job_id).values(dispatched_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)))
        await db.commit()
    recovered = await repo.recoverable(limit=10_000)
    assert pending.job_id in recovered and running.job_id not in recovered


@pytest.mark.asyncio
async def test_retry_is_bounded_and_failed_document_does_not_make_library_ready(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    _, kb_id, job = await queued_document(rag_sessions, rag_users[0], tmp_path)
    for attempt in range(3):
        claim = await repo.claim(job.job_id)
        assert claim is not None
        await repo.finish(claim, error_code="embedding_unavailable", retryable=True)
        async with rag_sessions() as db:
            # 模拟退避已过去，不使用真实等待或修改全局时间。
            await db.execute(update(RagJob).where(RagJob.id == job.job_id).values(lease_until=None))
            await db.commit()
    async with rag_sessions() as db:
        current = await db.get(RagJob, job.job_id)
        document = await db.get(KnowledgeDocument, job.document_id)
        kb = await db.get(KnowledgeBase, kb_id)
        assert current.status == "failed" and current.attempt == 3
        assert document.status == "failed" and kb.status == "empty"
    assert await repo.claim(job.job_id) is None


@pytest.mark.asyncio
async def test_uncertain_write_failure_keeps_cleanup_behind_cooldown(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    service, kb_id, job = await queued_document(rag_sessions, rag_users[0], tmp_path)
    claim = await repo.claim(job.job_id)
    await repo.finish(claim, error_code="vector_service_unavailable", retryable=True)
    async with rag_sessions() as db:
        cleanup = await service.delete_document(db, rag_users[0], kb_id, job.document_id)
    assert await repo.claim(cleanup.job_id) is None


@pytest.mark.asyncio
async def test_manual_cleanup_retry_does_not_reuse_old_attempt_generation(rag_sessions, rag_users, tmp_path: Path) -> None:
    repo = repository(rag_sessions)
    service, kb_id, job = await queued_document(rag_sessions, rag_users[0], tmp_path)
    async with rag_sessions() as db:
        cleanup = await service.delete_document(db, rag_users[0], kb_id, job.document_id)
        old = await db.get(RagJob, cleanup.job_id)
        old.status, old.attempt, old.error_code = "failed", 3, "vector_service_unavailable"
        await db.commit()
        retried = await service.delete_document(db, rag_users[0], kb_id, job.document_id)
    assert retried.job_id != cleanup.job_id
    assert await repo.claim(retried.job_id) is not None
