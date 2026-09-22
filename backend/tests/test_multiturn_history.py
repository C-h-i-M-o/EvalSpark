"""数据库历史装配验证模型分支隔离及实际输入快照。"""
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.conversation import ConversationContext
from app.models.model_config import ModelConfig, ModelProvider
from app.models.response import ModelResponse
from app.services.multiturn.history import load_branch_history, prepare_branch_context
from app.services.multiturn.store import ConversationError, ConversationStore


async def setup_history(sessions, owner: int) -> tuple[int, int, int, int]:
    """创建两个真实模型外键及两轮成功/失败混合记录。"""
    store = ConversationStore()
    async with sessions() as db:
        provider = ModelProvider(name=f"history-{uuid4().hex}", enabled=True)
        db.add(provider)
        await db.flush()
        models = [ModelConfig(provider_id=provider.id, model_name=name, display_name=name) for name in ("A", "B")]
        db.add_all(models)
        await db.flush()
        a, b = [model.id for model in models]
        conversation = await store.create(db, owner, mode="chat", title="历史隔离", visibility="public",
                                           config={"modelIds": [a, b], "inputBudget": 8192})
        first, _ = await store.reserve_turn(db, conversation.id, owner, prompt="用户<think>原文</think>",
                                             expected_turn=0, request_key="one")
        db.add_all([ModelResponse(task_id=first.task_id, model_config_id=a,
                                  answer_text="<think>内部分析</think>A第一轮", status="success"),
                    ModelResponse(task_id=first.task_id, model_config_id=b, answer_text="B第一轮", status="success")])
        await db.commit()
        await store.finish_generation(db, conversation.id, first.id, owner, status="completed")
        second, _ = await store.reserve_turn(db, conversation.id, owner, prompt="第二轮", expected_turn=1, request_key="two")
        db.add_all([ModelResponse(task_id=second.task_id, model_config_id=a, answer_text="A失败片段", status="failed"),
                    ModelResponse(task_id=second.task_id, model_config_id=b, answer_text="B第二轮", status="success")])
        await db.commit()
        await store.finish_generation(db, conversation.id, second.id, owner, status="completed")
        third, _ = await store.reserve_turn(db, conversation.id, owner, prompt="继续", expected_turn=2, request_key="three")
        return conversation.id, a, b, third.id


@pytest.mark.asyncio
async def test_branch_history_and_persisted_input(rag_sessions, rag_users) -> None:
    """原始历史保留思考供审计，模型实际输入剔除思考且不串模型回答。"""
    owner = rag_users[0]
    conversation, a, b, third = await setup_history(rag_sessions, owner)
    async with rag_sessions() as db:
        ah = await load_branch_history(db, conversation, owner, a, before_turn=3)
        bh = await load_branch_history(db, conversation, owner, b, before_turn=3)
        assert len(ah) == 2 and len(bh) == 4
        assert "内部分析" in ah[1].message.content
        assert all("B第一轮" not in item.message.content for item in ah)
    snapshot = await prepare_branch_context(rag_sessions, conversation, owner, third, a, system_prompt="系统要求")
    assert [message.content for message in snapshot.messages] == ["系统要求", "用户<think>原文</think>", "A第一轮", "继续"]
    async with rag_sessions() as db:
        saved = await db.scalar(select(ConversationContext).where(ConversationContext.turn_id == third))
        assert saved.source_hash and saved.snapshot_json["source_ids"] == list(snapshot.source_ids)
        assert saved.covered_through_turn == 2


@pytest.mark.asyncio
async def test_public_reader_and_unknown_branch_cannot_build(rag_sessions, rag_users) -> None:
    """公开查看不能构造供应商输入，未知模型也不能通过任意 ID 读取历史。"""
    owner, other = rag_users
    conversation, a, _, third = await setup_history(rag_sessions, owner)
    with pytest.raises(ConversationError) as error:
        await prepare_branch_context(rag_sessions, conversation, other, third, a, system_prompt="系统")
    assert error.value.status_code == 404
    async with rag_sessions() as db:
        with pytest.raises(ConversationError):
            await load_branch_history(db, conversation, owner, -1, before_turn=3)
        with pytest.raises(ConversationError):
            await load_branch_history(db, conversation, owner, a, before_turn=99)


@pytest.mark.asyncio
async def test_context_cannot_overwrite_attempt_or_prepare_completed_turn(rag_sessions, rag_users) -> None:
    """生成输入一经保存不可变；迟到上下文不能覆盖已经结束的轮次。"""
    owner = rag_users[0]
    conversation, a, _, third = await setup_history(rag_sessions, owner)
    await prepare_branch_context(rag_sessions, conversation, owner, third, a, system_prompt="系统")
    summarize = AsyncMock(side_effect=AssertionError("已有输入快照不得再次调用摘要"))
    cached = await prepare_branch_context(rag_sessions, conversation, owner, third, a,
        system_prompt="系统", summary_callback=summarize)
    assert cached.messages[-1].content == "继续"
    summarize.assert_not_called()
    with pytest.raises(ConversationError):
        await prepare_branch_context(rag_sessions, conversation, owner, third, a, system_prompt="另一系统")
    async with rag_sessions() as db:
        await ConversationStore().finish_generation(db, conversation, third, owner, status="completed")
    with pytest.raises(ConversationError):
        await prepare_branch_context(rag_sessions, conversation, owner, third, a, system_prompt="系统")
