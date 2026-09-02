"""MySQL 权威作业状态：短事务租约、同库串行与过期任务恢复。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, RagJob
from app.services.rag.errors import KnowledgeBaseError

LEASE_SECONDS = 300
RECOVERY_GRACE_SECONDS = 300
MAX_ATTEMPTS = 3
ACTIVE = ("queued", "running")


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    knowledge_base_id: int
    attempt: int


class JobRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def _now(self, db: AsyncSession) -> datetime:
        return await db.scalar(select(func.utc_timestamp()))

    async def _library_status(self, db: AsyncSession, kb: KnowledgeBase) -> None:
        if kb.status in ("deleting", "deleted", "failed", "reindex_required"):
            return
        active = await db.scalar(select(RagJob.id).where(RagJob.knowledge_base_id == kb.id, RagJob.status.in_(ACTIVE)).limit(1))
        deleting = await db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.status == "deleting").limit(1))
        if active or deleting:
            kb.status = "indexing"
        else:
            ready = await db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.status == "ready").limit(1))
            kb.status = "ready" if ready else "empty"
            kb.error_code = None

    async def _fail(self, db: AsyncSession, kb: KnowledgeBase, job: RagJob, code: str) -> None:
        job.status, job.error_code, job.lease_until = "failed", code, None
        if job.operation == "index":
            document = await db.get(KnowledgeDocument, job.document_id)
            if document and document.index_revision == job.target_revision and document.status not in ("deleting", "deleted"):
                document.status, document.error_code = "failed", code
        elif job.operation == "reindex" and kb.status not in ("deleting", "deleted"):
            kb.status, kb.error_code = "failed", code
            documents = (await db.scalars(select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.status.not_in(("ready", "deleting", "deleted")),
            ))).all()
            for document in documents:
                document.status, document.error_code = "failed", code
        elif job.operation.startswith("delete"):
            kb.error_code = code
        await self._library_status(db, kb)

    async def claim(self, job_id: str) -> JobClaim | None:
        async with self.sessions() as db:
            kb_id = await db.scalar(select(RagJob.knowledge_base_id).where(RagJob.id == job_id))
        if kb_id is None:
            return None
        async with self.sessions.begin() as db:
            # 所有写入统一先锁库，避免与 API 的库锁/作业锁顺序相反。
            kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id).with_for_update())
            job = await db.get(RagJob, job_id)
            now = await self._now(db)
            if job.status not in ACTIVE:
                return None
            if job.status == "running" and job.lease_until and job.lease_until + timedelta(seconds=RECOVERY_GRACE_SECONDS) > now:
                return None
            if job.status == "queued" and job.lease_until and job.lease_until > now:
                return None
            other = await db.scalar(select(RagJob.id).where(
                RagJob.knowledge_base_id == kb_id, RagJob.id != job_id,
                or_(RagJob.status == "running", RagJob.lease_until > now),
            ).limit(1))
            if other:
                return None
            if job.attempt >= MAX_ATTEMPTS:
                await self._fail(db, kb, job, "job_retry_exhausted")
                return None
            job.attempt += 1
            job.status, job.error_code = "running", None
            job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            return JobClaim(job.id, kb_id, job.attempt)

    @asynccontextmanager
    async def locked(self, claim: JobClaim) -> AsyncIterator[tuple[AsyncSession, KnowledgeBase, RagJob]]:
        async with self.sessions.begin() as db:
            kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == claim.knowledge_base_id).with_for_update())
            job = await db.get(RagJob, claim.job_id)
            now = await self._now(db)
            if job is None or job.status != "running" or job.attempt != claim.attempt or job.lease_until is None or job.lease_until <= now:
                raise KnowledgeBaseError("job_lease_lost", "作业租约已失效，旧执行器停止写入")
            job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            yield db, kb, job

    async def validate_target(self, db: AsyncSession, kb: KnowledgeBase, job: RagJob, document_id: int | None = None, revision: int | None = None) -> KnowledgeDocument | None:
        deleting = job.operation.startswith("delete")
        if not deleting and (kb.status in ("deleting", "deleted", "reindex_required") or job.operation == "reindex" and kb.content_revision != job.target_revision):
            raise KnowledgeBaseError("job_superseded", "知识库内容已变化，旧索引作业停止")
        if document_id is None:
            return None
        document = await db.get(KnowledgeDocument, document_id)
        if document is None or document.knowledge_base_id != kb.id or document.user_id != kb.user_id or document.index_revision != revision:
            raise KnowledgeBaseError("job_superseded", "文档版本已变化，旧索引作业停止")
        if not deleting and document.status in ("deleting", "deleted"):
            raise KnowledgeBaseError("job_superseded", "文档已开始删除，旧索引作业停止")
        return document

    async def checkpoint(self, claim: JobClaim, stage: str, *, document_id: int | None = None, revision: int | None = None, processed: int | None = None, total: int | None = None) -> None:
        async with self.locked(claim) as (db, kb, job):
            document = await self.validate_target(db, kb, job, document_id, revision)
            job.stage = stage
            if processed is not None:
                job.processed_count = processed
            if total is not None:
                job.total_count = total
            if document and stage in ("parsing", "embedding", "indexing"):
                document.status = stage

    async def renew(self, claim: JobClaim) -> None:
        async with self.locked(claim):
            pass

    async def finish(self, claim: JobClaim, *, error_code: str | None = None, retryable: bool = False) -> None:
        try:
            async with self.locked(claim) as (db, kb, job):
                if error_code and retryable and job.attempt < MAX_ATTEMPTS:
                    job.status, job.error_code, job.dispatched_at = "queued", error_code, None
                    # lease_until 对 queued 表示最早重试时间，重复消息也必须遵守退避。
                    job.lease_until = await self._now(db) + timedelta(seconds=RECOVERY_GRACE_SECONDS * job.attempt)
                elif error_code:
                    await self._fail(db, kb, job, error_code)
                    if retryable:
                        job.lease_until = await self._now(db) + timedelta(seconds=RECOVERY_GRACE_SECONDS)
                else:
                    job.status, job.stage, job.error_code, job.lease_until = "succeeded", "completed", None, None
                    await self._library_status(db, kb)
        except KnowledgeBaseError as error:
            if error.code != "job_lease_lost":
                raise

    async def recoverable(self, limit: int = 100) -> list[str]:
        async with self.sessions() as db:
            now = await self._now(db)
            return list((await db.scalars(select(RagJob.id).where(or_(
                and_(RagJob.status == "queued", or_(RagJob.lease_until.is_(None), RagJob.lease_until <= now), or_(RagJob.dispatched_at.is_(None), RagJob.dispatched_at <= now - timedelta(seconds=60))),
                and_(RagJob.status == "running", or_(RagJob.lease_until.is_(None), RagJob.lease_until <= now - timedelta(seconds=RECOVERY_GRACE_SECONDS))),
            )).order_by(RagJob.dispatched_at, RagJob.created_at, RagJob.id).limit(limit))).all())
