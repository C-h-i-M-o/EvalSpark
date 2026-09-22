"""多轮会话的权限和短事务持久化边界。"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationTurn
from app.models.evaluation import EvaluationTask
from app.services.rag.errors import KnowledgeBaseError


class ConversationError(KnowledgeBaseError):
    """沿用统一中文错误响应，不返回任何用户原文或凭据。"""


async def require_legacy_conversation(db: AsyncSession, conversation_id: int | None, user_id: int) -> None:
    """旧任务接口只可关联本人旧会话，新增多轮必须经过原子轮次接口。"""
    if conversation_id is None:
        return
    value = await db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
    if value is None:
        raise ConversationError("conversation_not_found", "会话不存在或无权访问", 404)
    if value.mode in ("chat", "rag"):
        raise ConversationError("conversation_managed", "多轮会话请通过续聊接口提交问题", 409)


def validate_snapshot(value: object) -> None:
    """递归拒绝密钥字段，快照只允许 JSON 可序列化的非秘密配置。"""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").replace("-", "").lower()
            if any(word in normalized for word in ("apikey", "password", "secret", "authorization", "credential")):
                raise ConversationError("conversation_secret_snapshot", "会话配置不能保存凭据", 422)
            validate_snapshot(item)
    elif isinstance(value, list):
        for item in value:
            validate_snapshot(item)
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ConversationError("conversation_invalid_snapshot", "会话配置必须为有效 JSON", 422) from error


class ConversationStore:
    """外部模型请求不得放在本模块的事务内。"""

    async def create(self, db: AsyncSession, user_id: int, *, mode: str, title: str,
                     visibility: str, config: dict[str, object],
                     knowledge_snapshot: dict[str, object] | None = None) -> Conversation:
        """保存已经过服务层模型可用性和知识库权限校验的配置。"""
        if mode not in ("chat", "rag") or visibility not in ("public", "private"):
            raise ConversationError("conversation_invalid_config", "会话模式或可见性无效", 422)
        if not title.strip() or len(title) > 120:
            raise ConversationError("conversation_invalid_title", "会话标题需要为 1 至 120 个字符", 422)
        validate_snapshot(config)
        validate_snapshot(knowledge_snapshot)
        conversation = Conversation(user_id=user_id, mode=mode, title=title.strip(), visibility=visibility,
            config_json=config, knowledge_snapshot_json=knowledge_snapshot, current_turn=0,
            generation_status="idle", updated_at=datetime.utcnow())
        db.add(conversation)
        await db.commit()
        return conversation

    async def get(self, db: AsyncSession, conversation_id: int, user_id: int, *,
                  owner_only: bool = False, lock: bool = False) -> Conversation:
        """读取时校验可见性，写操作必须只允许作者并使用当前锁定版本。"""
        statement = select(Conversation).where(Conversation.id == conversation_id,
            Conversation.mode.in_(("chat", "rag")))
        statement = statement.where(Conversation.user_id == user_id) if owner_only else statement.where(
            or_(Conversation.user_id == user_id, Conversation.visibility == "public"))
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        value = await db.scalar(statement)
        if value is None:
            raise ConversationError("conversation_not_found", "会话不存在或无权访问", 404)
        return value

    async def reserve_turn(self, db: AsyncSession, conversation_id: int, user_id: int, *, prompt: str,
                           expected_turn: int, request_key: str) -> tuple[ConversationTurn, bool]:
        """原子分配轮次和旧任务关联；重复请求返回既有结果，不再调用模型。"""
        if (not prompt.strip() or len(prompt.encode("utf-8")) > 65535
                or not request_key or len(request_key) > 64 or expected_turn < 0):
            raise ConversationError("conversation_invalid_turn", "问题、请求键或预期轮次无效", 422)
        digest = hashlib.sha256(json.dumps([prompt, expected_turn], ensure_ascii=False).encode("utf-8")).hexdigest()
        try:
            conversation = await self.get(db, conversation_id, user_id, owner_only=True, lock=True)
            existing = await db.scalar(select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation_id, ConversationTurn.request_key == request_key)
                .with_for_update().execution_options(populate_existing=True))
            if existing is not None:
                if existing.request_hash != digest:
                    raise ConversationError("conversation_request_conflict", "请求键已用于不同的问题或轮次", 409)
                await db.commit()
                return existing, False
            if conversation.current_turn != expected_turn or conversation.generation_status == "generating":
                raise ConversationError("conversation_turn_conflict", "会话已更新或仍在生成，请刷新后重试", 409)
            task = EvaluationTask(conversation_id=conversation_id, user_id=user_id, prompt=prompt,
                task_type=conversation.mode, status="pending", visibility=conversation.visibility)
            db.add(task)
            await db.flush()
            turn = ConversationTurn(conversation_id=conversation_id, task_id=task.id,
                turn_index=expected_turn + 1, request_key=request_key, request_hash=digest,
                prompt=prompt, generation_status="generating")
            db.add(turn)
            conversation.current_turn = turn.turn_index
            conversation.generation_status = "generating"
            conversation.updated_at = datetime.utcnow()
            await db.commit()
            return turn, True
        except Exception:
            await db.rollback()
            raise

    async def require_generating(self, db: AsyncSession, conversation_id: int, turn_id: int, user_id: int, *,
                                 generation_epoch: int = 1) -> ConversationTurn:
        """调用占位写入前锁定当前生成资格，恢复结束的轮次不能再开始调用。"""
        conversation = await self.get(db, conversation_id, user_id, owner_only=True, lock=True)
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id,
            ConversationTurn.conversation_id == conversation_id).with_for_update().execution_options(populate_existing=True))
        if (turn is None or conversation.generation_status != "generating"
                or turn.generation_status != "generating" or turn.turn_index != conversation.current_turn
                or turn.generation_epoch != generation_epoch):
            raise ConversationError("conversation_generation_ended", "轮次已经结束，不能继续生成", 409)
        return turn

    async def finish_generation(self, db: AsyncSession, conversation_id: int, turn_id: int, user_id: int, *,
                                status: Literal["completed", "failed", "interrupted"],
                                error_code: str | None = None, generation_epoch: int = 1) -> None:
        """只释放当前轮次的生成锁，后台评分状态由独立作业管理。"""
        try:
            conversation = await self.get(db, conversation_id, user_id, owner_only=True, lock=True)
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id,
                ConversationTurn.conversation_id == conversation_id).with_for_update().execution_options(populate_existing=True))
            if turn is None or turn.turn_index != conversation.current_turn:
                raise ConversationError("conversation_stale_completion", "不能修改已被后续轮次引用的历史状态", 409)
            if turn.generation_epoch != generation_epoch:
                await db.rollback()
                return
            if turn.generation_status != "generating":
                if turn.generation_status != status:
                    raise ConversationError("conversation_terminal_conflict", "轮次已经结束，不能覆盖其状态", 409)
                await db.commit()
                return
            turn.generation_status, turn.error_code = status, error_code
            turn.completed_at = datetime.utcnow()
            conversation.generation_status = "idle"
            conversation.updated_at = datetime.utcnow()
            await db.execute(update(EvaluationTask).where(EvaluationTask.id == turn.task_id).values(
                status=status, completed_at=turn.completed_at))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def set_visibility(self, db: AsyncSession, conversation_id: int, user_id: int, visibility: str) -> Conversation:
        """同事务更新会话与全部任务，防止新旧权限状态不一致。"""
        if visibility not in ("public", "private"):
            raise ConversationError("conversation_invalid_visibility", "会话可见性无效", 422)
        try:
            conversation = await self.get(db, conversation_id, user_id, owner_only=True, lock=True)
            conversation.visibility = visibility
            conversation.updated_at = datetime.utcnow()
            await db.execute(update(EvaluationTask).where(EvaluationTask.conversation_id == conversation_id).values(visibility=visibility))
            await db.commit()
            return conversation
        except Exception:
            await db.rollback()
            raise
