"""恢复已提交但漏投的消息；通知不是业务成功状态。"""

import logging
from collections.abc import Callable

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models.knowledge_base import RagJob
from app.services.rag.jobs import JobRepository

logger = logging.getLogger(__name__)


async def recover_jobs(sessions: async_sessionmaker[AsyncSession], publisher: Callable[[str], bool], *, limit: int = 100) -> None:
    for job_id in await JobRepository(sessions).recoverable(limit):
        try:
            if not await run_in_threadpool(publisher, job_id):
                return
            async with sessions.begin() as db:
                await db.execute(update(RagJob).where(RagJob.id == job_id).values(dispatched_at=func.utc_timestamp()))
        except Exception:
            logger.warning("RAG 恢复投递暂未完成，将在下一轮重试")
            return


async def recover_once() -> None:
    from app.services.knowledge_base_service import publish_rag_job
    # Celery 每次使用独立事件循环，连接池不能跨循环或跨 fork 复用。
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        await recover_jobs(async_sessionmaker(engine, expire_on_commit=False), publish_rag_job)
    finally:
        await engine.dispose()
