"""索引作业按短事务与外部操作交替执行，只有完整版本可以发布。"""

import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool
from tokenizers import Tokenizer

from app.core.config import settings
from app.services.embedding_config_service import get_config, runtime_config
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.services.rag.clients import EmbeddingClient, RagClientError, VectorStore, load_tokenizer
from app.services.rag.documents import DocumentChunk, parse_in_subprocess, resolve_storage_path, split_blocks
from app.services.rag.errors import KnowledgeBaseError
from app.services.rag.jobs import JobClaim, JobRepository

MAX_LIBRARY_CHUNKS = 100_000


class IndexRunner:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, vectors: VectorStore, embedding: EmbeddingClient, tokenizer: Tokenizer | None = None, documents_dir: Path = settings.rag_documents_dir, resolve_config: bool = False) -> None:
        self.sessions = sessions
        self.jobs = JobRepository(sessions)
        self.vectors = vectors
        self.embedding = embedding
        self.tokenizer = tokenizer
        self.documents_dir = documents_dir
        self.resolve_config = resolve_config

    async def _heartbeat(self, claim: JobClaim) -> None:
        while True:
            await asyncio.sleep(30)
            await self.jobs.renew(claim)

    async def run(self, job_id: str) -> None:
        claim = await self.jobs.claim(job_id)
        if claim is None:
            return
        work = asyncio.create_task(self._execute(claim))
        heartbeat = asyncio.create_task(self._heartbeat(claim))
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
            await self.jobs.finish(claim)
        except KnowledgeBaseError as error:
            await self.jobs.finish(claim, error_code=error.code, retryable=error.status_code == 503)
        except RagClientError as error:
            await self.jobs.finish(claim, error_code=error.code, retryable=error.retryable)
        except (SQLAlchemyError, OSError):
            await self.jobs.finish(claim, error_code="index_storage_unavailable", retryable=True)
        except Exception:
            # 不把异常参数中的文档原文、路径、数据库地址写入日志或状态。
            await self.jobs.finish(claim, error_code="index_processing_failed")
        finally:
            for task in (work, heartbeat):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def _execute(self, claim: JobClaim) -> None:
        if self.resolve_config:
            async with self.sessions() as db:
                row = await get_config(db)
                # 删除不依赖服务可用性，停用 Embedding 后仍必须能删除资料。
                disabled = bool(row is not None and row.settings_json and not row.settings_json.get("enabled"))
                config = runtime_config(row, allow_disabled=True)
                self.embedding = EmbeddingClient(config)
                self.vectors.config = config
        async with self.jobs.locked(claim) as (db, kb, job):
            await self.jobs.validate_target(db, kb, job)
            operation, user_id, chunk_size, overlap = job.operation, kb.user_id, kb.chunk_size, kb.chunk_overlap
            query = select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.user_id == user_id)
            if job.document_id is not None:
                query = query.where(KnowledgeDocument.id == job.document_id)
            elif not operation.startswith("delete"):
                query = query.where(KnowledgeDocument.status.not_in(("deleting", "deleted")))
            documents = list((await db.scalars(query.order_by(KnowledgeDocument.id))).all())
            if job.document_id is not None and (not documents or documents[0].index_revision != job.target_revision):
                raise KnowledgeBaseError("job_superseded", "文档版本已变化，旧作业停止")
        if operation.startswith("delete"):
            for document in documents:
                await self._delete_document(claim, document)
            if operation == "delete_knowledge_base":
                async with self.jobs.locked(claim) as (_, kb, _):
                    kb.status, kb.error_code = "deleted", None
            return
        if self.resolve_config and disabled:
            raise KnowledgeBaseError("embedding_disabled", "管理员尚未启用 Embedding 服务")
        await self.vectors.ensure_collection()
        for document in documents:
            if document.status != "ready":
                await self._index_document(claim, document, chunk_size, overlap)
            await self._cleanup_old(claim, document)

    async def _checkpoint(self, claim: JobClaim, document: KnowledgeDocument, stage: str, *, processed: int | None = None, total: int | None = None) -> None:
        await self.jobs.checkpoint(claim, stage, document_id=document.id, revision=document.index_revision, processed=processed, total=total)

    async def _index_document(self, claim: JobClaim, document: KnowledgeDocument, chunk_size: int, overlap: int) -> None:
        await self._checkpoint(claim, document, "parsing", processed=0, total=0)
        path = resolve_storage_path(document.storage_key, root=self.documents_dir)
        blocks = await parse_in_subprocess(path, document.media_type)
        await self._checkpoint(claim, document, "splitting")
        remote = isinstance(self.embedding, EmbeddingClient) and self.embedding.config.rag_embedding_protocol == "openai"
        tokenizer = None if remote else (self.tokenizer or await run_in_threadpool(load_tokenizer))
        chunks = await run_in_threadpool(split_blocks, blocks, tokenizer, chunk_size, overlap)
        del blocks
        await self._reserve_chunks(claim, document, chunks)
        ids = [str(uuid5(NAMESPACE_URL, f"evalspark:chunk:{document.id}:{document.index_revision}:{index}")) for index in range(len(chunks))]
        # 先保存全部正文，再写向量。每批单独提交，避免大文档长期锁住知识库。
        for start in range(0, len(chunks), 128):
            async with self.jobs.locked(claim) as (db, kb, job):
                await self.jobs.validate_target(db, kb, job, document.id, document.index_revision)
                rows = [{
                    "id": ids[index], "document_id": document.id, "knowledge_base_id": kb.id, "user_id": kb.user_id,
                    "index_revision": document.index_revision, "chunk_index": index, "text": chunks[index].text,
                    "token_count": chunks[index].token_count, "source_json": chunks[index].source.model_dump(mode="json"),
                } for index in range(start, min(start + 128, len(chunks)))]
                statement = insert(KnowledgeChunk).values(rows)
                await db.execute(statement.on_duplicate_key_update(text=statement.inserted.text, token_count=statement.inserted.token_count, source_json=statement.inserted.source_json))
        for start in range(0, len(chunks), 16):
            batch = chunks[start:start + 16]
            await self._checkpoint(claim, document, "embedding", processed=start, total=len(chunks))
            vectors = await self.embedding.embed([chunk.text for chunk in batch], "document")
            # 外部 Embedding 期间可能收到删除，写向量前再次检查。
            await self._checkpoint(claim, document, "indexing")
            await self.vectors.upsert(document.user_id, document.knowledge_base_id, document.id, document.index_revision, [(ids[index], index) for index in range(start, start + len(batch))], vectors)
        await self._checkpoint(claim, document, "verifying", processed=len(chunks), total=len(chunks))
        count = await self.vectors.count(document.user_id, document.knowledge_base_id, document.id, document.index_revision)
        if count != len(chunks):
            raise RagClientError("index_count_mismatch", "向量数量与目标块数不一致，索引未发布", retryable=True)
        async with self.jobs.locked(claim) as (db, kb, job):
            current = await self.jobs.validate_target(db, kb, job, document.id, document.index_revision)
            stored = await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id, KnowledgeChunk.index_revision == document.index_revision))
            if stored != len(chunks):
                raise KnowledgeBaseError("index_count_mismatch", "正文数量与目标块数不一致，索引未发布")
            current.status, current.error_code, current.chunk_count = "ready", None, len(chunks)

    async def _reserve_chunks(self, claim: JobClaim, document: KnowledgeDocument, chunks: list[DocumentChunk]) -> None:
        async with self.jobs.locked(claim) as (db, kb, job):
            current = await self.jobs.validate_target(db, kb, job, document.id, document.index_revision)
            published = await db.scalar(select(func.sum(KnowledgeDocument.chunk_count)).where(
                KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id != document.id, KnowledgeDocument.status == "ready",
            )) or 0
            if published + len(chunks) > MAX_LIBRARY_CHUNKS:
                raise KnowledgeBaseError("chunk_limit", "每个知识库最多发布 100,000 个文本块", 413)
            current.chunk_count = len(chunks)

    async def _cleanup_old(self, claim: JobClaim, document: KnowledgeDocument) -> None:
        await self._checkpoint(claim, document, "cleanup_old")
        await self.vectors.delete(document.user_id, document.knowledge_base_id, document.id, except_revision=document.index_revision)
        async with self.jobs.locked(claim) as (db, kb, job):
            await self.jobs.validate_target(db, kb, job, document.id, document.index_revision)
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id, KnowledgeChunk.index_revision != document.index_revision))

    async def _delete_document(self, claim: JobClaim, document: KnowledgeDocument) -> None:
        await self._checkpoint(claim, document, "deleting_vectors")
        await self.vectors.delete(document.user_id, document.knowledge_base_id, document.id)
        await self._checkpoint(claim, document, "deleting_file")
        await run_in_threadpool(resolve_storage_path(document.storage_key, root=self.documents_dir).unlink, missing_ok=True)
        async with self.jobs.locked(claim) as (db, kb, job):
            current = await self.jobs.validate_target(db, kb, job, document.id, document.index_revision)
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
            current.status, current.error_code, current.chunk_count = "deleted", None, 0
