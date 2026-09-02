import importlib
import io
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from starlette.datastructures import Headers, UploadFile
from tokenizers import Tokenizer, models, pre_tokenizers

from app.core.config import Settings
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, RagJob
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBasePatch
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag.clients import EmbeddingClient, RagClientError, VectorStore


@pytest_asyncio.fixture
async def index_setup(rag_sessions, rag_users, tmp_path: Path):
    assert importlib.util.find_spec("app.services.rag.indexing") is not None, "尚未实现异步索引流水线"
    runner_type = importlib.import_module("app.services.rag.indexing").IndexRunner
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "word": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    calls: list[int] = []
    async def embed(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["inputs"]
        calls.append(len(inputs))
        return httpx.Response(200, json=[[1.0] + [0.0] * 1023 for _ in inputs])
    vector_client = AsyncQdrantClient(":memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(embed)) as http:
        vectors = VectorStore(vector_client)
        embedding = EmbeddingClient(Settings(_env_file=None), http_client=http, tokenizer=tokenizer)
        runner = runner_type(rag_sessions, vectors=vectors, embedding=embedding, tokenizer=tokenizer, documents_dir=tmp_path)
        service = KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False)
        async with rag_sessions() as db:
            kb = await service.create(db, rag_users[0], KnowledgeBaseCreate(name="索引流水线", chunk_size=128, chunk_overlap=20))
            upload = UploadFile(io.BytesIO(("word " * 400).encode()), filename="文档.txt", headers=Headers({"content-type": "text/plain"}))
            accepted = await service.upload(db, rag_users[0], kb.id, upload)
        yield runner, service, kb.id, accepted, calls
    await vector_client.close()


@pytest.mark.asyncio
async def test_index_publishes_complete_chunks_once(index_setup, rag_sessions, rag_users) -> None:
    runner, _, kb_id, accepted, calls = index_setup
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        document = await db.get(KnowledgeDocument, accepted.document_id)
        kb = await db.get(KnowledgeBase, kb_id)
        job = await db.get(RagJob, accepted.job_id)
        chunks = (await db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))).all()
        assert document.status == kb.status == "ready" and job.status == "succeeded"
        assert document.chunk_count == len(chunks) > 1
        assert await runner.vectors.count(rag_users[0], kb_id, document.id, 1) == len(chunks)
        assert all(chunk.text and chunk.source_json["kind"] == "text" and chunk.token_count <= 128 for chunk in chunks)
        before = list(calls)
    await runner.run(accepted.job_id)
    assert calls == before


@pytest.mark.asyncio
async def test_delete_cleans_vector_text_and_original_file(index_setup, rag_sessions, rag_users) -> None:
    runner, service, kb_id, accepted, _ = index_setup
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        document = await db.get(KnowledgeDocument, accepted.document_id)
        original = runner.documents_dir / document.storage_key
        cleanup = await service.delete_document(db, rag_users[0], kb_id, document.id)
    await runner.run(cleanup.job_id)
    assert not original.exists()
    assert await runner.vectors.count(rag_users[0], kb_id, accepted.document_id) == 0
    async with rag_sessions() as db:
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status == "deleted"
        assert (await db.get(KnowledgeBase, kb_id)).status == "empty"
        assert await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == accepted.document_id)) == 0


@pytest.mark.asyncio
async def test_delete_during_embedding_prevents_late_publication(index_setup, rag_sessions, rag_users) -> None:
    runner, service, kb_id, accepted, _ = index_setup
    deletion = []
    async def embed(request: httpx.Request) -> httpx.Response:
        # 此处能取得同库行锁也证明外部调用期间没有持有 MySQL 事务锁。
        async with rag_sessions() as db:
            deletion.append(await service.delete_document(db, rag_users[0], kb_id, accepted.document_id))
        return httpx.Response(200, json=[[1.0] + [0.0] * 1023 for _ in json.loads(request.content)["inputs"]])
    async with httpx.AsyncClient(transport=httpx.MockTransport(embed)) as http:
        runner.embedding.http_client = http
        await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        job = await db.get(RagJob, accepted.job_id)
        assert job.status == "failed" and job.error_code == "job_superseded"
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status == "deleting"
    await runner.run(deletion[0].job_id)
    assert await runner.vectors.count(rag_users[0], kb_id, accepted.document_id) == 0


@pytest.mark.asyncio
async def test_failure_after_upsert_is_not_published_and_retry_is_idempotent(index_setup, rag_sessions, rag_users, monkeypatch) -> None:
    runner, _, kb_id, accepted, _ = index_setup
    original_count = runner.vectors.count
    async def fail_count(*args, **kwargs):
        raise RagClientError("vector_service_unavailable", "模拟数量核对失败", retryable=True)
    monkeypatch.setattr(runner.vectors, "count", fail_count)
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status != "ready"
        job = await db.get(RagJob, accepted.job_id)
        assert job.status == "queued"
        job.lease_until = None
        await db.commit()
    monkeypatch.setattr(runner.vectors, "count", original_count)
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        document = await db.get(KnowledgeDocument, accepted.document_id)
        assert document.status == "ready"
        assert await original_count(rag_users[0], kb_id, document.id) == document.chunk_count
        assert await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)) == document.chunk_count


@pytest.mark.asyncio
async def test_reindex_publishes_new_version_and_removes_old_chunks(index_setup, rag_sessions, rag_users) -> None:
    runner, service, kb_id, accepted, _ = index_setup
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        await service.patch(db, rag_users[0], kb_id, KnowledgeBasePatch(chunk_size=200))
        reindex = await service.reindex(db, rag_users[0], kb_id)
    await runner.run(reindex.job_id)
    async with rag_sessions() as db:
        document = await db.get(KnowledgeDocument, accepted.document_id)
        assert document.status == "ready" and document.index_revision == 2
        assert (await db.get(KnowledgeBase, kb_id)).status == "ready"
        versions = set((await db.scalars(select(KnowledgeChunk.index_revision).where(KnowledgeChunk.document_id == document.id))).all())
        assert versions == {2}
    assert await runner.vectors.count(rag_users[0], kb_id, document.id, 1) == 0


@pytest.mark.asyncio
async def test_chunk_quota_failure_never_publishes_partial_index(index_setup, rag_sessions, monkeypatch) -> None:
    from app.services.rag import indexing
    runner, _, kb_id, accepted, _ = index_setup
    monkeypatch.setattr(indexing, "MAX_LIBRARY_CHUNKS", 1)
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        job = await db.get(RagJob, accepted.job_id)
        assert job.status == "failed" and job.error_code == "chunk_limit"
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status == "failed"
        assert (await db.get(KnowledgeBase, kb_id)).status == "empty"


@pytest.mark.asyncio
async def test_cleanup_failure_retains_file_and_deleting_tombstone(index_setup, rag_sessions, rag_users, monkeypatch) -> None:
    runner, service, kb_id, accepted, _ = index_setup
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        document = await db.get(KnowledgeDocument, accepted.document_id)
        original = runner.documents_dir / document.storage_key
        cleanup = await service.delete_document(db, rag_users[0], kb_id, document.id)
    async def unavailable(*args, **kwargs):
        raise RagClientError("vector_service_unavailable", "模拟向量故障", retryable=True)
    monkeypatch.setattr(runner.vectors, "delete", unavailable)
    await runner.run(cleanup.job_id)
    assert original.exists()
    async with rag_sessions() as db:
        assert (await db.get(RagJob, cleanup.job_id)).status == "queued"
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status == "deleting"
        assert (await db.get(KnowledgeBase, kb_id)).status == "indexing"


@pytest.mark.asyncio
async def test_reindex_failure_blocks_old_configuration(index_setup, rag_sessions, rag_users) -> None:
    runner, service, kb_id, accepted, _ = index_setup
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        await service.patch(db, rag_users[0], kb_id, KnowledgeBasePatch(chunk_size=200))
        document = await db.get(KnowledgeDocument, accepted.document_id)
        (runner.documents_dir / document.storage_key).unlink()
        rebuilt = await service.reindex(db, rag_users[0], kb_id)
    await runner.run(rebuilt.job_id)
    async with rag_sessions() as db:
        kb = await db.get(KnowledgeBase, kb_id)
        assert kb.status == "failed" and kb.error_code == "invalid_file"
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status == "failed"
        assert (await db.get(RagJob, rebuilt.job_id)).status == "failed"


@pytest.mark.asyncio
async def test_mysql_chunk_write_rollback_never_sends_partial_vectors(index_setup, rag_sessions, rag_users) -> None:
    runner, _, kb_id, accepted, calls = index_setup
    engine = rag_sessions.kw["bind"].sync_engine
    failed = False
    def interrupt_chunk_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal failed
        if not failed and statement.startswith("INSERT INTO knowledge_chunks"):
            failed = True
            raise OperationalError(statement, None, RuntimeError("模拟写块后提交前断开"))
    event.listen(engine, "after_cursor_execute", interrupt_chunk_insert)
    try:
        await runner.run(accepted.job_id)
    finally:
        event.remove(engine, "after_cursor_execute", interrupt_chunk_insert)
    assert failed and calls == []
    assert await runner.vectors.count(rag_users[0], kb_id, accepted.document_id) == 0
    async with rag_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == accepted.document_id)) == 0
        assert (await db.get(RagJob, accepted.job_id)).status == "queued"
        assert (await db.get(KnowledgeDocument, accepted.document_id)).status != "ready"


@pytest.mark.asyncio
async def test_successful_manual_cleanup_retry_clears_library_error(index_setup, rag_sessions, rag_users) -> None:
    runner, service, kb_id, accepted, _ = index_setup
    await runner.run(accepted.job_id)
    async with rag_sessions() as db:
        cleanup = await service.delete_document(db, rag_users[0], kb_id, accepted.document_id)
        job = await db.get(RagJob, cleanup.job_id)
        job.status, job.attempt = "failed", 3
        job.error_code = "vector_service_unavailable"
        kb = await db.get(KnowledgeBase, kb_id)
        kb.error_code = job.error_code
        await db.commit()
        retried = await service.delete_document(db, rag_users[0], kb_id, accepted.document_id)
    await runner.run(retried.job_id)
    async with rag_sessions() as db:
        kb = await db.get(KnowledgeBase, kb_id)
        assert kb.status == "empty" and kb.error_code is None
