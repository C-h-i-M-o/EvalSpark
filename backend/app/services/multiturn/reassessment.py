"""从已有固定输入创建一次正式复评，重复提交不重复收费。"""

import json
import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationAssessment
from app.schemas.conversation import AssessmentRead
from app.services.multiturn.assessment_reader import project_assessment
from app.services.multiturn.assessments import AssessmentStore
from app.services.multiturn.dispatch import publish_assessment
from app.services.multiturn.judge import JudgePacket
from app.services.multiturn.assessment_batches import snapshot_batches
from app.services.multiturn.store import ConversationError, ConversationStore

logger = logging.getLogger(__name__)


async def submit_reassessment(db: AsyncSession, conversation_id: int, owner: int,
                              source_assessment_id: int) -> AssessmentRead:
    """先鉴权并锁定会话，再从不可变暂定快照提交幂等正式作业。"""
    try:
        conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True, lock=True)
        source = await db.scalar(select(ConversationAssessment).where(
            ConversationAssessment.id == source_assessment_id,
            ConversationAssessment.conversation_id == conversation_id))
        if source is None:
            raise ConversationError("assessment_not_found", "评分作业不存在或无权访问", 404)
        if source.formal or source.status in ("queued", "running"):
            raise ConversationError("assessment_source_pending", "请选择已经结束的暂定评分进行正式复评", 409)
        if source.score_version != f"{conversation.mode}-multiturn-v1":
            raise ConversationError("assessment_version_changed", "评分版本不兼容，不能沿用旧检查项复评", 409)
        snapshot = source.input_json
        packet = JudgePacket.model_validate_json(json.dumps(snapshot["packet"]))
        job = await AssessmentStore().enqueue(db, conversation_id, owner,
            operation_key=f"formal:{source.id}", model_config_id=source.model_config_id,
            packet=packet, formal=True, input_budget=snapshot["inputBudget"],
            currency=snapshot["currency"], response_id=source.response_id,
            batches=snapshot_batches(snapshot) if "batches" in snapshot else None)
        result = project_assessment(job)
    except Exception:
        await db.rollback()
        raise
    if result.status == "queued":
        try:
            await run_in_threadpool(publish_assessment, result.id)
        except Exception:
            logger.warning("正式复评投递失败，作业已保存等待恢复扫描")
    return result
