"""隔离数据库验证历史引用的会话、分支、原文和版本边界。"""
import pytest
from sqlalchemy import select

from app.models.conversation import ConversationTurn
from app.models.response import ModelResponse
from app.models.rag import RagResponseDetail
from app.models.knowledge_base import KnowledgeBase
from app.schemas.conversation import TurnCreate
from app.services.multiturn.rag_reference_store import load_historical_references
from app.services.multiturn.store import ConversationError
from test_multiturn_rag_generation import setup_generator


@pytest.mark.asyncio
async def test_references_resolve_only_own_branch_and_actual_cited_snapshot(rag_sessions, rag_users, monkeypatch):
    """同题引用在两分支解析成不同回答 ID，未引用标签和非作者被拒绝。"""
    owner, reader = rag_users[:2]
    generator, conversation_id, identities, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    _ = [event async for event in generator.stream(conversation_id, owner,
        TurnCreate(prompt="首轮", expectedTurn=0, requestKey="reference-base"))]
    async with rag_sessions() as db:
        a = await load_historical_references(db, conversation_id, owner, identities[0], before_turn=2, prompt="解释[T1:S1]")
        b = await load_historical_references(db, conversation_id, owner, identities[1], before_turn=2, prompt="解释上一轮[S1]")
        assert len(a) == len(b) == 1 and a[0].response_id != b[0].response_id
        assert a[0].evidence.text == "固定资料"
        recent = await load_historical_references(db, conversation_id, owner, identities[0], before_turn=2, prompt="刚才的[S1]")
        assert recent == a
        for actor, model, prompt in ((reader, identities[0], "[T1:S1]"), (owner, identities[2], "[T1:S1]"), (owner, identities[0], "[T1:S2]")):
            with pytest.raises(ConversationError):
                await load_historical_references(db, conversation_id, actor, model, before_turn=2, prompt=prompt)


@pytest.mark.asyncio
async def test_history_thinking_and_changed_knowledge_do_not_authorize_reference(rag_sessions, rag_users, monkeypatch):
    """思考中的标签不算最终回答引用；资料版本变化后不能靠旧快照续聊。"""
    owner = rag_users[0]
    generator, conversation_id, identities, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    _ = [event async for event in generator.stream(conversation_id, owner,
        TurnCreate(prompt="首轮", expectedTurn=0, requestKey="reference-version"))]
    async with rag_sessions() as db:
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))
        response = await db.scalar(select(ModelResponse).where(ModelResponse.task_id == turn.task_id, ModelResponse.model_config_id == identities[0]))
        response.answer_text = "<think>[S1]</think>无引用"
        await db.commit()
        with pytest.raises(ConversationError):
            await load_historical_references(db, conversation_id, owner, identities[0], before_turn=2, prompt="[T1:S1]")
        detail = await db.get(RagResponseDetail, response.id)
        knowledge = await db.get(KnowledgeBase, detail.knowledge_base_id)
        knowledge.content_revision += 1
        await db.commit()
        with pytest.raises(ConversationError) as changed:
            await load_historical_references(db, conversation_id, owner, identities[1], before_turn=2, prompt="[T1:S1]")
        assert changed.value.code == "knowledge_base_changed"
