"""失败分支的重试与跳过：所有状态变更在短事务内完成。"""

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationAssessment, ConversationTurn, ConversationUsage, ConversationContext
from app.models.evaluation import EvaluationResult, EvaluationTask
from app.models.response import ModelResponse
from app.schemas.conversation import BranchActionCreate
from app.schemas.rag import RagModelSnapshot
from app.services.model_config_service import RuntimeModelConfig
from app.services.multiturn.store import ConversationError
from app.services.rag.usage import model_snapshot


def _payload_hash(payload: BranchActionCreate) -> str:
    """生成稳定的请求内容哈希，用于 requestKey 冲突检测。"""
    value = [payload.turn_id, payload.model_config_id, payload.request_key, payload.action]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode("utf-8")).hexdigest()


async def reserve_branch_action(
    db: AsyncSession,
    conversation_id: int,
    owner: int,
    payload: BranchActionCreate,
    model: RuntimeModelConfig | None,
) -> tuple[ConversationTurn, ModelResponse, int, bool]:
    """锁定会话并为失败分支预留重试/跳过回答，重复请求只返回原记录。"""
    operation_key = f"branch:{payload.request_key}"
    digest = _payload_hash(payload)
    try:
        conversation = await db.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == owner).with_for_update().execution_options(populate_existing=True))
        if conversation is None:
            raise ConversationError("conversation_not_found", "会话不存在或无权访问", 404)
        if (conversation.mode not in ("chat", "rag")
                or payload.model_config_id not in (conversation.config_json or {}).get("modelIds", [])
                or (model is not None and model.id != payload.model_config_id)):
            raise ConversationError("conversation_invalid_branch", "模型分支不属于当前会话", 422)
        if model is None:
            if payload.action != "skip":
                raise ConversationError("conversation_invalid_branch", "重试需要可用的冻结模型配置", 422)
            snapshots = [item for item in (conversation.config_json or {}).get("models", [])
                if item.get("modelConfigId") == payload.model_config_id]
            if len(snapshots) != 1:
                raise ConversationError("conversation_config_invalid", "模型快照缺失或重复", 409)
            frozen = RagModelSnapshot.model_validate_json(json.dumps(snapshots[0]))
        else:
            frozen = model_snapshot(model)
        model_id = payload.model_config_id

        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.conversation_id == conversation_id,
            ConversationUsage.operation_key == operation_key).with_for_update())
        if usage is not None:
            detail = usage.detail_json or {}
            if detail.get("payloadHash") != digest:
                raise ConversationError("conversation_request_conflict", "请求键已用于不同的分支操作", 409)
            turn_id = detail.get("turnId")
            response_id = detail.get("responseId")
            if not isinstance(turn_id, int) or not isinstance(response_id, int):
                raise ConversationError("branch_action_corrupt", "分支操作记录不完整", 409)
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id))
            response = await db.get(ModelResponse, response_id)
            if turn is None or response is None or turn.conversation_id != conversation_id or response.task_id != turn.task_id:
                raise ConversationError("branch_action_corrupt", "分支操作记录不完整", 409)
            attempt = detail.get("attempt", 1)
            await db.commit()
            return turn, response, int(attempt), False

        if conversation.generation_status != "idle":
            raise ConversationError("conversation_generation_active", "会话仍在生成中，不能操作失败分支", 409)
        turn = await db.scalar(select(ConversationTurn).where(
            ConversationTurn.id == payload.turn_id,
            ConversationTurn.conversation_id == conversation_id,
            ConversationTurn.turn_index == conversation.current_turn,
            ConversationTurn.generation_status.in_(("completed", "interrupted")),
        ).with_for_update())
        if turn is None:
            raise ConversationError("branch_turn_invalid", "只能操作最新已结束轮次", 409)

        responses = list((await db.scalars(select(ModelResponse).where(
            ModelResponse.task_id == turn.task_id,
            ModelResponse.model_config_id == payload.model_config_id).order_by(ModelResponse.id))).all())
        if not responses:
            raise ConversationError("branch_response_missing", "该分支没有可恢复的回答", 409)
        latest = responses[-1]
        latest_snapshot = latest.config_snapshot or {}
        if latest.status != "failed" or latest_snapshot.get("branchAction") == "skip":
            raise ConversationError("branch_response_not_failed", "只能恢复最新失败回答", 409)

        assessment = await db.scalar(select(ConversationAssessment).where(
            ConversationAssessment.conversation_id == conversation_id,
            ConversationAssessment.response_id.is_(None),
            ConversationAssessment.through_turn >= turn.turn_index,
        ).limit(1))
        if assessment is not None:
            raise ConversationError("branch_assessment_active", "该轮已形成会话报告，不能恢复", 409)

        if payload.action == "retry":
            requirement_usage = await db.scalar(select(ConversationUsage).where(
                ConversationUsage.conversation_id == conversation_id,
                ConversationUsage.stage == "requirements",
                ConversationUsage.detail_json["turnId"].as_integer() == turn.id,
                ConversationUsage.detail_json["requirementsSaved"].as_boolean().is_(True),
            ).limit(1))
            if requirement_usage is None:
                raise ConversationError("branch_requirements_missing", "原轮次要求尚未保存，不能重试", 409)

        attempt_values = [1]
        attempt_values.extend((item.config_snapshot or {}).get("conversationAttempt", 1) for item in responses)
        attempt_values.extend((await db.scalars(select(ConversationContext.attempt).where(
            ConversationContext.turn_id == turn.id, ConversationContext.model_config_id == model_id))).all())
        attempt = max(value for value in attempt_values if type(value) is int and value > 0) + 1
        snapshot = frozen.model_dump(mode="json", by_alias=True)
        snapshot.update({"conversationAttempt": attempt, "branchAction": payload.action})
        response = ModelResponse(task_id=turn.task_id, model_config_id=model_id, status="pending" if payload.action == "retry" else "failed",
            config_snapshot=snapshot, currency=frozen.currency,
            error_message=None if payload.action == "retry" else "用户已明确跳过本轮回答")
        db.add(response)
        await db.flush()
        if payload.action == "skip":
            db.add(EvaluationResult(response_id=response.id, score_status="model_failed", final_score=None, excluded_from_stats=True))
            conversation.updated_at = datetime.utcnow()
        else:
            turn.generation_status, turn.completed_at, turn.error_code = "generating", None, None
            turn.generation_epoch = getattr(turn, "generation_epoch", 1) + 1
            conversation.generation_status, conversation.updated_at = "generating", datetime.utcnow()
            task = await db.get(EvaluationTask, turn.task_id)
            if task is not None:
                task.status, task.completed_at = "pending", None
        db.add(ConversationUsage(conversation_id=conversation_id, user_id=owner, operation_key=operation_key,
            stage="branch_action", status="completed", total_tokens=0, currency=frozen.currency, cost=0,
            accounted=True, detail_json={"payloadHash": digest, "turnId": turn.id, "modelConfigId": model_id,
                                        "action": payload.action, "responseId": response.id, "attempt": attempt}))
        await db.commit()
        return turn, response, attempt, True
    except Exception:
        await db.rollback()
        raise


async def settle_branch_action(
    db: AsyncSession,
    conversation_id: int,
    owner: int,
    turn_id: int,
    response_id: int,
    generation_epoch: int,
) -> None:
    """在重试上下文中断时收尾预留回答，避免 pending 回答阻塞后续恢复。"""
    from app.services.multiturn.generation_recovery import _settle_response

    try:
        conversation = await db.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == owner).with_for_update())
        turn = await db.scalar(select(ConversationTurn).where(
            ConversationTurn.conversation_id == conversation_id, ConversationTurn.id == turn_id).with_for_update())
        if (conversation is None or turn is None or conversation.generation_status != "generating"
                or turn.generation_status != "generating"
                or getattr(turn, "generation_epoch", 1) != generation_epoch):
            await db.rollback()
            return
        response = await db.scalar(select(ModelResponse).where(
            ModelResponse.id == response_id, ModelResponse.task_id == turn.task_id).with_for_update())
        if response is not None and response.status not in ("success", "failed"):
            await _settle_response(db, conversation, response)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
