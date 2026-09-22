"""隔离数据库验证要求版本可重建且不能改写旧来源。"""
import pytest
from sqlalchemy import select

from app.models.conversation import ConversationRequirement
from app.services.multiturn.requirements import Requirement, active_requirements
from app.services.multiturn.requirement_store import load_requirements, save_requirements
from app.services.multiturn.store import ConversationStore, ConversationError


@pytest.mark.asyncio
async def test_requirement_versions_preserve_old_score_inputs(rag_sessions, rag_users):
    """预算修改保存新版本，按第一轮读取仍得到原始预算。"""
    owner = rag_users[0]
    store = ConversationStore()
    async with rag_sessions() as db:
        conversation = await store.create(db, owner, mode="chat", title="要求版本", visibility="public", config={"modelIds": [1]})
        conversation_id = conversation.id
        first, _ = await store.reserve_turn(db, conversation.id, owner, prompt="预算5000", expected_turn=0, request_key="one")
        old = Requirement(id="budget", text="预算5000", quote="预算5000", source_id=f"turn:{first.id}:user",
                          source_turn=1, scope="conversation", critical=True, ambiguous=False)
        await save_requirements(db, conversation.id, owner, turn_id=first.id, records=(old,))
        await save_requirements(db, conversation.id, owner, turn_id=first.id, records=(old,))
        await store.finish_generation(db, conversation.id, first.id, owner, status="completed")
        second, _ = await store.reserve_turn(db, conversation.id, owner, prompt="预算改为7000", expected_turn=1, request_key="two")
        new = Requirement(id="budget-new", text="预算7000", quote="预算改为7000", source_id=f"turn:{second.id}:user",
                          source_turn=2, scope="conversation", critical=True, ambiguous=False, supersedes=("budget",))
        updated = old.model_copy(update={"retired_at": 2})
        await save_requirements(db, conversation.id, owner, turn_id=second.id, records=(updated, new))
        earlier = await load_requirements(db, conversation.id, owner, through_turn=1)
        latest = await load_requirements(db, conversation.id, owner, through_turn=2)
        assert earlier == (old,)
        assert [item.text for item in active_requirements(latest, 2)] == ["预算7000"]
        versions = list((await db.scalars(select(ConversationRequirement).where(
            ConversationRequirement.conversation_id == conversation.id))).all())
        assert len(versions) == 3
        with pytest.raises(ConversationError):
            await save_requirements(db, conversation.id, owner, turn_id=second.id,
                                    records=(updated.model_copy(update={"text": "篡改"}), new))
        await db.rollback()
        with pytest.raises(ConversationError) as error:
            await load_requirements(db, conversation_id, rag_users[1], through_turn=2)
        assert error.value.status_code == 404
