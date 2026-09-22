"""从数据库构建模型独立历史，并保存本次生成使用的上下文。"""
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ChatMessage
from app.adapters.base import ModelClient
from app.services.model_config_service import RuntimeModelConfig
from app.services.multiturn.summary import ModelSummarizer
from app.services.multiturn.summary_usage import SummaryUsageRecorder
from app.models.conversation import ConversationContext, ConversationTurn
from app.models.response import ModelResponse
from app.services.multiturn.context import (
    ContextBudget, ContextInput, ContextSnapshot, HistoryTurn, SummaryResult, build_context_async,
)
from app.services.multiturn.store import ConversationError, ConversationStore


async def prepare_context_with_summary(sessions: async_sessionmaker[AsyncSession], conversation_id: int,
                                       owner: int, turn_id: int, model_id: int, *, system_prompt: str,
                                       summary_model: RuntimeModelConfig, summary_client: ModelClient,
                                       summary_input_budget: int, memory_budget: int, attempt: int = 1,
                                       memory: tuple[ChatMessage, ...] = (), generation_epoch: int = 1) -> ContextSnapshot:
    """连接摘要、额度及输入快照，生成协调者负责传入已校验的冻结模型配置。"""
    usage = SummaryUsageRecorder(sessions, conversation_id=conversation_id, owner=owner, turn_id=turn_id,
                                branch_id=model_id, attempt=attempt, model=summary_model, generation_epoch=generation_epoch)
    summarizer = ModelSummarizer(summary_client, input_budget=summary_input_budget,
        output_tokens=summary_model.max_tokens, memory_budget=memory_budget,
        before_call=usage.before_call, after_call=usage.after_call)
    return await prepare_branch_context(sessions, conversation_id, owner, turn_id, model_id,
        system_prompt=system_prompt, summary_callback=summarizer, memory=memory, attempt=attempt, generation_epoch=generation_epoch)


async def load_branch_history(db: AsyncSession, conversation_id: int, owner: int, model_id: int,
                              *, before_turn: int) -> tuple[HistoryTurn, ...]:
    """作者续聊只读取自己的模型历史，排除失败、未完成和目标轮及以后内容。"""
    conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True)
    if type(model_id) is not int or model_id not in (conversation.config_json or {}).get("modelIds", []):
        raise ConversationError("conversation_invalid_branch", "模型分支不属于当前会话", 422)
    if type(before_turn) is not int or not 1 <= before_turn <= conversation.current_turn + 1:
        raise ConversationError("conversation_invalid_turn", "历史截止轮次无效", 422)
    rows = (await db.execute(select(ConversationTurn, ModelResponse).join(
        ModelResponse, ModelResponse.task_id == ConversationTurn.task_id).where(
            ConversationTurn.conversation_id == conversation_id,
            ConversationTurn.turn_index < before_turn,
            ConversationTurn.generation_status.in_(("completed", "interrupted")),
            ModelResponse.model_config_id == model_id,
            ModelResponse.status == "success",
        ).order_by(ConversationTurn.turn_index, ModelResponse.id))).all()
    seen: set[int] = set()
    messages: list[HistoryTurn] = []
    branch = f"{conversation_id}:{model_id}"
    for turn, response in rows:
        if turn.id in seen:
            raise ConversationError("conversation_ambiguous_history", "同轮模型存在多个成功回答，无法确定历史", 409)
        seen.add(turn.id)
        if not response.answer_text.strip():
            continue
        messages.extend((
            HistoryTurn(f"turn:{turn.id}:user", branch, turn.turn_index, ChatMessage("user", turn.prompt)),
            HistoryTurn(f"response:{response.id}", branch, turn.turn_index, ChatMessage("assistant", response.answer_text)),
        ))
    return tuple(messages)


async def prepare_branch_context(sessions: async_sessionmaker[AsyncSession], conversation_id: int,
                                  owner: int, turn_id: int, model_id: int, *, system_prompt: str,
                                  summary_callback: Callable[[tuple[HistoryTurn, ...]], Awaitable[SummaryResult]] | None = None,
                                  memory: tuple[ChatMessage, ...] = (), attempt: int = 1, generation_epoch: int = 1) -> ContextSnapshot:
    """在事务外构建或压缩，随后锁定轮次保存实际输入供生成与审计使用。"""
    if type(attempt) is not int or attempt < 1:
        raise ValueError("生成尝试次数必须为正整数")
    async with sessions() as db:
        conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True)
        turn = await db.scalar(select(ConversationTurn).where(
            ConversationTurn.id == turn_id, ConversationTurn.conversation_id == conversation_id))
        if turn is None or turn.generation_status != "generating" or turn.turn_index != conversation.current_turn or turn.generation_epoch != generation_epoch:
            raise ConversationError("conversation_turn_conflict", "当前轮次不再处于生成状态", 409)
        history = await load_branch_history(db, conversation_id, owner, model_id, before_turn=turn.turn_index)
        config = conversation.config_json or {}
        budget = config.get("inputBudget", 8192)
        prompt, turn_index = turn.prompt, turn.turn_index
        preparation_hash = hashlib.sha256(json.dumps({"history": [asdict(item) for item in history],
            "memory": [asdict(item) for item in memory], "system": system_prompt,
            "prompt": prompt, "budget": budget}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        cached = await db.scalar(select(ConversationContext).where(ConversationContext.turn_id == turn_id,
            ConversationContext.model_config_id == model_id, ConversationContext.attempt == attempt))
        if cached is not None:
            saved = cached.snapshot_json
            if saved.get("preparation_hash") != preparation_hash:
                raise ConversationError("conversation_context_conflict", "同次生成的上下文快照不可覆盖", 409)
            summary = saved.get("summary")
            return ContextSnapshot(messages=tuple(ChatMessage(**message) for message in saved["messages"]),
                compressed=saved["compressed"], estimated_tokens=saved["estimated_tokens"],
                source_branch_id=saved["source_branch_id"], source_ids=tuple(saved["source_ids"]),
                compression_error=saved.get("compression_error"), target_reached=saved["target_reached"],
                summary=SummaryResult(summary["content"], tuple(summary["source_ids"])) if summary else None)
    context = await build_context_async(ContextInput(branch_id=f"{conversation_id}:{model_id}",
        current_prompt=prompt, history=history, memory=memory, system_prompt=system_prompt,
        budget=ContextBudget(max_tokens=budget), summary_callback=summary_callback))
    snapshot = asdict(context)
    # JSON 往返将元组规范化为数组，以便数据库读回后做精确幂等比较。
    snapshot = json.loads(json.dumps(snapshot, ensure_ascii=False))
    snapshot["preparation_hash"] = preparation_hash
    source_hash = hashlib.sha256(json.dumps([asdict(item) for item in history],
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    async with sessions() as db:
        conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True, lock=True)
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id)
                              .with_for_update().execution_options(populate_existing=True))
        if turn is None or turn.generation_status != "generating" or conversation.current_turn != turn_index or turn.generation_epoch != generation_epoch:
            raise ConversationError("conversation_turn_conflict", "上下文准备期间轮次已结束", 409)
        existing = await db.scalar(select(ConversationContext).where(ConversationContext.turn_id == turn_id,
            ConversationContext.model_config_id == model_id, ConversationContext.attempt == attempt))
        if existing is not None:
            if existing.source_hash != source_hash or existing.snapshot_json != snapshot:
                raise ConversationError("conversation_context_conflict", "同次生成的上下文快照不可覆盖", 409)
        else:
            db.add(ConversationContext(conversation_id=conversation_id, turn_id=turn_id, model_config_id=model_id,
                attempt=attempt, covered_through_turn=turn_index - 1, source_hash=source_hash, snapshot_json=snapshot))
        await db.commit()
    return context
