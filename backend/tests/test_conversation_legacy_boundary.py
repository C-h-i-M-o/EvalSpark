"""旧任务入口不得绕过多轮会话的权限、轮次及统一可见性。"""

import pytest
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.evaluation import EvaluationTask
from app.schemas.evaluation import EvaluationTaskCreate
from app.services.evaluation_service import evaluation_service
from app.services.multiturn.store import ConversationError, ConversationStore


@pytest.mark.asyncio
async def test_legacy_creation_cannot_attach_managed_or_other_users_conversation(rag_sessions, rag_users) -> None:
    """旧入口可关联本人旧会话，不能向新多轮或他人会话注入任务。"""
    owner, other = rag_users
    async with rag_sessions() as db:
        managed = await ConversationStore().create(db, owner, mode="chat", title="受管理", visibility="private", config={})
        legacy = Conversation(user_id=other, title="其他用户", mode="compare")
        db.add(legacy)
        await db.commit()
        managed_id, legacy_id = managed.id, legacy.id
        for conversation_id in (managed_id, legacy_id):
            with pytest.raises(ConversationError):
                await evaluation_service._create_task_record(db, EvaluationTaskCreate(prompt="绕过", modelIds=[1],
                    conversationId=conversation_id), owner)
            await db.rollback()
        assert not list((await db.scalars(select(EvaluationTask).where(EvaluationTask.conversation_id.in_([managed_id, legacy_id])))).all())


@pytest.mark.asyncio
async def test_legacy_visibility_updates_whole_managed_conversation(rag_sessions, rag_users, monkeypatch) -> None:
    """从旧历史详情修改一轮可见性时同样统一整个会话权限。"""
    from unittest.mock import AsyncMock
    owner = rag_users[0]
    store = ConversationStore()
    async with rag_sessions() as db:
        conversation = await store.create(db, owner, mode="chat", title="统一权限", visibility="private", config={})
        first, _ = await store.reserve_turn(db, conversation.id, owner, prompt="一", expected_turn=0, request_key="a")
        await store.finish_generation(db, conversation.id, first.id, owner, status="completed")
        second, _ = await store.reserve_turn(db, conversation.id, owner, prompt="二", expected_turn=1, request_key="b")
        first_id, second_id, conversation_id = first.task_id, second.task_id, conversation.id
        monkeypatch.setattr(evaluation_service, "get_task", AsyncMock(return_value=None))
        await evaluation_service.update_task_visibility(first_id, "public", db, owner)
        await db.rollback()
        value = await db.get(Conversation, conversation_id)
        tasks = list((await db.scalars(select(EvaluationTask).where(EvaluationTask.id.in_([first_id, second_id])))).all())
        assert value.visibility == "public" and all(task.visibility == "public" for task in tasks)
