"""评审任务的队列入口及运行时配置校验。"""
import json
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.conversation import Conversation, ConversationAssessment
from app.schemas.rag import RagModelSnapshot
from app.services.model_config_service import RuntimeModelConfig, model_config_service
from app.services.multiturn.assessments import execute_assessment
from app.services.multiturn.catalog import model_identity
from app.services.multiturn.store import ConversationError
from app.services.rag.evaluation import create_client


def publish_assessment(job_id: int) -> bool:
    """仅发送数据库标识，投递失败由数据库保留待处理状态。"""
    if type(job_id) is not int or job_id <= 0:
        raise ValueError("评分作业标识无效")
    from app.worker import celery_app
    celery_app.send_task("app.worker.run_conversation_assessment", args=[job_id], queue="rag", retry=False)
    return True


async def resolve_judge(db: AsyncSession, conversation: Conversation) -> RuntimeModelConfig:
    """重新校验模型目标与可用性，保持原计费快照且允许凭据轮换。"""
    config = conversation.config_json or {}
    judge_id = config.get("judgeModelId")
    candidates = config.get("modelIds")
    identities = config.get("identities")
    snapshots = config.get("models")
    if (type(judge_id) is not int or not isinstance(candidates, list)
            or not candidates or not isinstance(identities, dict) or not isinstance(snapshots, list)):
        raise ConversationError("assessment_config_invalid", "会话缺少完整评审配置", 409)
    judge = await model_config_service.resolve_runtime_model(db, judge_id)
    if judge_id in candidates or identities.get(str(judge.id)) != model_identity(judge):
        raise ConversationError("assessment_config_changed", "评审目标已变化或与候选相同", 409)
    for model_id in candidates:
        if type(model_id) is not int:
            raise ConversationError("assessment_config_invalid", "候选配置无效", 409)
        candidate = await model_config_service.resolve_runtime_model(db, model_id)
        if identities.get(str(candidate.id)) != model_identity(candidate):
            raise ConversationError("assessment_config_changed", "候选目标已变化", 409)
        if (candidate.provider_name, candidate.model_name) == (judge.provider_name, judge.model_name):
            raise ConversationError("assessment_self_judge", "评审不能使用候选相同供应商和模型", 409)
    saved = [item for item in snapshots if isinstance(item, dict) and item.get("modelConfigId") == judge_id]
    if len(saved) != 1:
        raise ConversationError("assessment_config_invalid", "评审计费快照缺失或重复", 409)
    snapshot = RagModelSnapshot.model_validate_json(json.dumps(saved[0]))
    return replace(judge, currency=snapshot.currency, input_price=snapshot.price_input,
        output_price=snapshot.price_output, cache_hit_price=snapshot.price_cache_hit,
        cache_creation_price=snapshot.price_cache_creation, max_tokens=snapshot.max_tokens,
        temperature=snapshot.temperature, timeout_seconds=snapshot.timeout_seconds)


async def run_assessment_job(sessions: async_sessionmaker[AsyncSession], job_id: int) -> bool:
    """从持久化关系获取作者和模型，消息不能指定另一个用户或供应商。"""
    async with sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        if job is None or job.status != "queued":
            return False
        conversation = await db.get(Conversation, job.conversation_id)
        try:
            if conversation is None or conversation.user_id is None:
                raise ValueError("评分会话缺失")
            owner = conversation.user_id
            judge = await resolve_judge(db, conversation)
            if job.input_json.get("currency") != judge.currency:
                raise ValueError("评分币种与冻结配置不一致")
        except Exception:
            # 不持久化供应商异常文本或配置，避免凭据经任务结果泄漏。
            await db.rollback()
            current = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                .with_for_update().execution_options(populate_existing=True))
            if current is not None and current.status == "queued":
                from datetime import datetime
                current.status = "judge_failed"
                current.result_json = {"errorCode": "assessment_config_unavailable"}
                current.completed_at = datetime.utcnow()
                await db.commit()
            return False
    return await execute_assessment(sessions, job_id, owner, create_client(judge),
                                    max_output_tokens=judge.max_tokens)
