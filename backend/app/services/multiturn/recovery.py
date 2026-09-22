"""恢复漏投评分并结束超时运行；不重试已开始的收费调用。"""
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.models.conversation import Conversation, ConversationAssessment
from app.services.multiturn.assessments import AssessmentStore

logger = logging.getLogger(__name__)


async def recover_assessments(sessions: async_sessionmaker[AsyncSession], publisher: Callable[[int], bool],
                              *, limit: int = 20, now: datetime | None = None) -> None:
    """有界扫描，未迁移数据库直接跳过，投递故障留待下一次恢复。"""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("恢复批量大小必须为 1 至 100")
    cutoff = (now or datetime.utcnow()) - timedelta(hours=4)
    async with sessions() as db:
        connection = await db.connection()
        available = await connection.run_sync(lambda sync: inspect(sync).has_table("conversation_assessments"))
        if not available:
            return
        stale = (await db.execute(select(ConversationAssessment.id, Conversation.user_id)
            .join(Conversation, Conversation.id == ConversationAssessment.conversation_id)
            .where(ConversationAssessment.status == "running", ConversationAssessment.started_at < cutoff)
            .order_by(ConversationAssessment.id).limit(limit))).all()
    for job_id, owner in stale:
        if owner is None:
            continue
        async with sessions() as db:
            # 锁定后复核，已完成作业或刚被其他恢复者处理的作业不能被覆盖。
            job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                                  .with_for_update().execution_options(populate_existing=True))
            if job is not None and job.status == "running" and job.started_at is not None and job.started_at < cutoff:
                await AssessmentStore().interrupt(db, job_id, owner)
    async with sessions() as db:
        queued = list((await db.scalars(select(ConversationAssessment.id)
            .where(ConversationAssessment.status == "queued")
            .order_by(ConversationAssessment.id).limit(limit))).all())
    for job_id in queued:
        try:
            if not await run_in_threadpool(publisher, job_id):
                return
        except Exception:
            logger.warning("多轮评分投递暂不可用，数据库作业仍待处理")
            return
