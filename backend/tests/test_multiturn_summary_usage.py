"""隔离 MySQL 验证实际压缩回调、快照缓存与摘要用量入账。"""
import pytest
from sqlalchemy import select

from app.models.conversation import Conversation, ConversationTurn, ConversationUsage
from app.models.response import ModelResponse
from app.models.token_usage import DailyUserTokenUsage
from app.services.multiturn.history import prepare_context_with_summary
from app.services.multiturn.store import ConversationStore, ConversationError
from app.services.multiturn.summary_usage import SummaryUsageRecorder
from test_multiturn_history import setup_history
from test_multiturn_summary import SummaryClient
from test_rag_evaluation import model


@pytest.mark.asyncio
async def test_real_summary_path_accounts_each_batch_and_reuses_context(rag_sessions, rag_users) -> None:
    """完整装配链触发分批摘要，调用用量和快照落库，再次准备不付费。"""
    owner = rag_users[0]
    conversation_id, a, _, third_id = await setup_history(rag_sessions, owner)
    store = ConversationStore()
    async with rag_sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        conversation.config_json = {**conversation.config_json, "summaryModelId": a}
        first = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id,
                                                               ConversationTurn.turn_index == 1))
        first.prompt = "预算7000元。" + "背景" * 2000
        third = await db.get(ConversationTurn, third_id)
        db.add(ModelResponse(task_id=third.task_id, model_config_id=a, answer_text="继续处理", status="success"))
        await db.commit()
        await store.finish_generation(db, conversation_id, third_id, owner, status="completed")
        fourth, _ = await store.reserve_turn(db, conversation_id, owner, prompt="补充", expected_turn=3, request_key="four")
        db.add(ModelResponse(task_id=fourth.task_id, model_config_id=a, answer_text="补充完成", status="success"))
        await db.commit()
        await store.finish_generation(db, conversation_id, fourth.id, owner, status="completed")
        fifth, _ = await store.reserve_turn(db, conversation_id, owner, prompt="汇总", expected_turn=4, request_key="five")
        fifth_id = fifth.id
    client = SummaryClient()
    arguments = dict(system_prompt="正常回答", summary_model=model(a), summary_client=client,
                     summary_input_budget=3000, memory_budget=600)
    result = await prepare_context_with_summary(rag_sessions, conversation_id, owner, fifth_id, a, **arguments)
    assert result.compressed and result.estimated_tokens <= 8192
    count = len(client.requests)
    assert count > 1
    async with rag_sessions() as db:
        usages = list((await db.scalars(select(ConversationUsage).where(
            ConversationUsage.conversation_id == conversation_id))).all())
        assert len(usages) == count
        assert all(usage.accounted and usage.status == "completed" for usage in usages)
        daily = await db.scalar(select(DailyUserTokenUsage).where(DailyUserTokenUsage.user_id == owner))
        assert daily.total_tokens == count * 10
    cached = await prepare_context_with_summary(rag_sessions, conversation_id, owner, fifth_id, a, **arguments)
    assert cached == result and len(client.requests) == count


@pytest.mark.asyncio
async def test_summary_duplicate_start_and_unknown_finish(rag_sessions, rag_users) -> None:
    """重复开始不获调用许可，中断后的未知用量不伪造零入账。"""
    owner = rag_users[0]
    conversation_id, a, _, third = await setup_history(rag_sessions, owner)
    async with rag_sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        conversation.config_json = {**conversation.config_json, "summaryModelId": a}
        await db.commit()
    recorder = SummaryUsageRecorder(rag_sessions, conversation_id=conversation_id, owner=owner,
                                   turn_id=third, branch_id=a, attempt=1, model=model(a))
    await recorder.before_call(1)
    with pytest.raises(ConversationError, match="已经开始"):
        await recorder.before_call(1)
    async with rag_sessions() as db:
        await ConversationStore().finish_generation(db, conversation_id, third, owner, status="interrupted")
    await recorder.after_call(1, None)
    await recorder.after_call(1, None)
    async with rag_sessions() as db:
        usage = await db.scalar(select(ConversationUsage).where(ConversationUsage.conversation_id == conversation_id))
        assert usage.status == "unknown" and usage.total_tokens is None and not usage.accounted
        assert await db.scalar(select(DailyUserTokenUsage).where(DailyUserTokenUsage.user_id == owner)) is None
