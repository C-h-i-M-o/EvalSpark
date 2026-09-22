"""将分批摘要的实际调用关联到轮次、分支和日额度。"""
from dataclasses import asdict
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ModelReply
from app.models.conversation import ConversationTurn, ConversationUsage
from app.models.user import User
from app.services.model_config_service import RuntimeModelConfig
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.rag.usage import estimate_stage_cost, model_snapshot
from app.services.token_quota_service import token_quota_service


class SummaryUsageRecorder:
    """提供摘要器的开始/结束回调；外部请求不占用事务锁。"""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, conversation_id: int, owner: int,
                 turn_id: int, branch_id: int, attempt: int, model: RuntimeModelConfig,
                 stage: Literal["summary", "requirements", "rewrite_summary", "answer_summary"] = "summary",
                 generation_epoch: int = 1) -> None:
        """冻结本次摘要的归属与价格，调用方负责解析模型的最新凭据。"""
        if any(type(value) is not int or value <= 0 for value in
               (conversation_id, owner, turn_id, branch_id, attempt)):
            raise ValueError("摘要计费归属与尝试次数无效")
        self.sessions = sessions
        self.conversation_id, self.owner = conversation_id, owner
        self.turn_id, self.branch_id, self.attempt = turn_id, branch_id, attempt
        self.model = model_snapshot(model)
        self.generation_epoch = generation_epoch
        if stage not in ("summary", "requirements", "rewrite_summary", "answer_summary"):
            raise ValueError("上下文处理阶段无效")
        self.stage = stage

    def _key(self, index: int) -> str:
        """生成跨进程可重现的阶段唯一键，不使用正文或凭据。"""
        if type(index) is not int or index < 1:
            raise ValueError("摘要批次必须为正整数")
        key = f"{self.stage}:{self.turn_id}:{self.branch_id}:{self.attempt}:{index}"
        if len(key) > 96:
            raise ValueError("摘要操作标识超过存储长度")
        return key

    async def before_call(self, index: int) -> None:
        """确认当前生成资格和额度，提交调用占位后才允许访问供应商。"""
        key = self._key(index)
        async with self.sessions() as db:
            conversation = await ConversationStore().get(db, self.conversation_id, self.owner,
                                                         owner_only=True, lock=True)
            config = conversation.config_json or {}
            if (self.branch_id not in config.get("modelIds", [])
                    or self.model.model_config_id != config.get("summaryModelId")):
                raise ConversationError("summary_model_conflict", "摘要模型或候选分支与会话配置不一致", 409)
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == self.turn_id,
                ConversationTurn.conversation_id == self.conversation_id))
            if (turn is None or turn.generation_status != "generating"
                    or turn.turn_index != conversation.current_turn or turn.generation_epoch != self.generation_epoch):
                raise ConversationError("summary_turn_conflict", "当前轮次已结束，不能继续摘要", 409)
            user = await db.get(User, self.owner, populate_existing=True)
            if user is None or user.status != "active":
                raise ConversationError("summary_user_disabled", "用户不存在或已停用", 403)
            await token_quota_service.ensure_can_start(db, user)
            existing = await db.scalar(select(ConversationUsage.id).where(
                ConversationUsage.conversation_id == self.conversation_id, ConversationUsage.operation_key == key))
            if existing is not None:
                raise ConversationError("summary_already_started", "该摘要批次已经开始，不能重复调用", 409)
            db.add(ConversationUsage(conversation_id=self.conversation_id, user_id=self.owner,
                operation_key=key, stage=self.stage, status="pending", currency=self.model.currency,
                detail_json={"model": self.model.model_dump(mode="json", by_alias=True),
                    "turnId": self.turn_id, "branchId": self.branch_id, "attempt": self.attempt, "batch": index},
                accounted=False))
            await db.commit()

    async def after_call(self, index: int, reply: ModelReply | None) -> None:
        """即使生成已中断仍记录已发生费用，重复回调不再次累计。"""
        key = self._key(index)
        async with self.sessions() as db:
            usage = await db.scalar(select(ConversationUsage).where(
                ConversationUsage.conversation_id == self.conversation_id,
                ConversationUsage.user_id == self.owner, ConversationUsage.operation_key == key,
            ).with_for_update().execution_options(populate_existing=True))
            if usage is None:
                raise ConversationError("summary_not_started", "摘要调用尚未登记", 409)
            if usage.status != "pending":
                return
            known = reply is not None and reply.usage_known
            usage.status = "completed" if known else "unknown"
            usage.total_tokens = reply.usage.total_tokens if known else None
            usage.cost = estimate_stage_cost(self.model, reply.usage).total_cost if known else None
            usage.detail_json = {**usage.detail_json, "usage": asdict(reply.usage) if known else None,
                                 "latencyMs": reply.latency_ms if reply else None}
            await db.flush()
            await token_quota_service.record_conversation_usage(db, usage_id=usage.id, user_id=self.owner)
            await db.commit()
