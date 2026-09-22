"""按心跳收尾失活生成，保留回答与费用，不重放任何收费请求。"""
from datetime import datetime, timedelta

from sqlalchemy import func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.conversation import Conversation, ConversationTurn, ConversationUsage
from app.models.evaluation import EvaluationResult, EvaluationTask
from app.models.rag import RagResponseDetail
from app.models.response import ModelResponse
from app.schemas.rag import RagStageUsage
from app.services.token_quota_service import token_quota_service

GENERATION_STALE_SECONDS = 4 * 60 * 60


async def recover_generations(sessions: async_sessionmaker[AsyncSession], *, limit: int = 20,
                              now: datetime | None = None) -> None:
    """有界选取失活候选，每个会话独立事务复核并原子释放，未迁移库跳过。"""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("恢复批量大小必须为 1 至 100")
    moment = now or datetime.utcnow()
    cutoff = moment - timedelta(seconds=GENERATION_STALE_SECONDS)
    async with sessions() as db:
        connection = await db.connection()
        if not await connection.run_sync(lambda sync: inspect(sync).has_table("conversation_turns")):
            return
        ids = (await db.scalars(select(Conversation.id).where(Conversation.mode.in_(("chat", "rag")),
            Conversation.generation_status == "generating",
            func.coalesce(Conversation.updated_at, Conversation.created_at) < cutoff)
            .order_by(Conversation.id).limit(limit))).all()
    for identity in ids:
        async with sessions() as db, db.begin():
            conversation = await db.scalar(select(Conversation).where(Conversation.id == identity)
                .with_for_update().execution_options(populate_existing=True))
            if (conversation is None or conversation.user_id is None or conversation.generation_status != "generating"
                    or (conversation.updated_at or conversation.created_at) >= cutoff):
                continue
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == identity,
                ConversationTurn.turn_index == conversation.current_turn).with_for_update())
            if turn is None or turn.generation_status != "generating":
                continue
            task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == turn.task_id,
                EvaluationTask.user_id == conversation.user_id, EvaluationTask.conversation_id == identity,
                EvaluationTask.task_type == conversation.mode).with_for_update())
            if task is None:
                continue
            responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id == task.id)
                .order_by(ModelResponse.id).with_for_update())).all()
            keys = [f"generate:{response.id}" for response in responses]
            usages = (await db.scalars(select(ConversationUsage).where(ConversationUsage.conversation_id == identity,
                ConversationUsage.user_id == conversation.user_id,
                ConversationUsage.stage.in_(("generate", "summary", "requirements", "rewrite_summary", "answer_summary")),
                or_(ConversationUsage.detail_json["turnId"].as_integer() == turn.id,
                    ConversationUsage.operation_key.in_(keys))).order_by(ConversationUsage.id).with_for_update())).all()
            # 先锁本轮所有阶段流水，再入账，避免摘要回调持有流水锁等待同一个日额度行。
            for response in responses:
                await _settle_response(db, conversation, response)
            for usage in usages:
                if usage.status == "pending":
                    usage.status, usage.total_tokens, usage.cost = "unknown", None, None
                    usage.detail_json = {**usage.detail_json, "errorCode": "generation_heartbeat_expired"}
                await db.flush()
                await token_quota_service.record_conversation_usage(db, usage_id=usage.id, user_id=conversation.user_id)
            turn.generation_status, turn.error_code, turn.completed_at = "interrupted", "generation_heartbeat_expired", moment
            task.status, task.completed_at = "interrupted", moment
            conversation.generation_status, conversation.updated_at = "idle", moment


async def _settle_response(db: AsyncSession, conversation: Conversation, response: ModelResponse) -> None:
    """保存已完成分支，未完成回答置失败，RAG 阶段已知费用按原有方法幂等入账。"""
    if response.status != "success":
        response.status = "failed"
        response.error_message = response.error_message or "生成进程失活，本分支已中断"
    if await db.scalar(select(EvaluationResult.id).where(EvaluationResult.response_id == response.id)) is None:
        db.add(EvaluationResult(response_id=response.id, final_score=None, excluded_from_stats=True,
            score_status="judge_disabled" if response.status == "success" else "model_failed",
            judge_prompt_version="rag-multiturn-v1" if conversation.mode == "rag" else None))
    if conversation.mode == "rag":
        detail = await db.scalar(select(RagResponseDetail).where(RagResponseDetail.response_id == response.id).with_for_update())
        if detail is not None:
            stages = [RagStageUsage.model_validate(item) for item in detail.stage_usage_json]
            for stage in stages:
                if stage.status == "pending":
                    stage.status = "unknown"
            detail.stage_usage_json = [stage.model_dump(mode="json", by_alias=True) for stage in stages]
            if response.status != "success":
                detail.failure_stage = detail.failure_stage or "answer"
                detail.error_code = detail.error_code or "generation_heartbeat_expired"
            await db.flush()
            await token_quota_service.record_rag_usage(db, response_id=response.id, user_id=conversation.user_id)
