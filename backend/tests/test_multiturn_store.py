"""在独立 MySQL 库验证会话权限、轮次锁和请求幂等。"""

import asyncio

import pytest
from sqlalchemy import select

from app.models.evaluation import EvaluationTask
from app.models.conversation import ConversationTurn
from app.services.multiturn.store import ConversationStore, ConversationError


async def create_conversation(sessions, owner: int, visibility: str = "private") -> int:
    """创建不包含供应商密钥的测试会话配置。"""
    async with sessions() as db:
        conversation = await ConversationStore().create(db, owner, mode="chat", title="多轮测试",
            visibility=visibility, config={"modelIds": [1], "judgeModelId": 2})
        return conversation.id


@pytest.mark.asyncio
async def test_private_and_public_read_do_not_grant_append(rag_sessions, rag_users) -> None:
    """公开仅授予读取权，其他用户和管理员仍不能追加轮次。"""
    owner, other = rag_users
    store = ConversationStore()
    conversation_id = await create_conversation(rag_sessions, owner)
    async with rag_sessions() as db:
        with pytest.raises(ConversationError) as error:
            await store.get(db, conversation_id, other)
        assert error.value.status_code == 404
        await db.rollback()
        await store.set_visibility(db, conversation_id, owner, "public")
    async with rag_sessions() as db:
        assert (await store.get(db, conversation_id, other)).id == conversation_id
        with pytest.raises(ConversationError) as error:
            await store.reserve_turn(db, conversation_id, other, prompt="越权", expected_turn=0, request_key="other")
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_key_replays_and_changed_payload_conflicts(rag_sessions, rag_users) -> None:
    """相同请求重放既有轮次，不同正文不能复用同一个请求键。"""
    owner = rag_users[0]
    store = ConversationStore()
    conversation_id = await create_conversation(rag_sessions, owner)
    async with rag_sessions() as db:
        first, created = await store.reserve_turn(db, conversation_id, owner, prompt="问题", expected_turn=0, request_key="same")
        assert created and first.turn_index == 1
        again, created = await store.reserve_turn(db, conversation_id, owner, prompt="问题", expected_turn=0, request_key="same")
        assert not created and again.id == first.id and again.task_id == first.task_id
        with pytest.raises(ConversationError) as error:
            await store.reserve_turn(db, conversation_id, owner, prompt="另一个问题", expected_turn=0, request_key="same")
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_next_turn_has_one_winner(rag_sessions, rag_users) -> None:
    """两个标签页同时追加时只有一个请求能获得生成资格。"""
    owner = rag_users[0]
    store = ConversationStore()
    conversation_id = await create_conversation(rag_sessions, owner)

    async def reserve(key: str) -> str:
        """使用独立事务模拟并行请求。"""
        async with rag_sessions() as db:
            try:
                await store.reserve_turn(db, conversation_id, owner, prompt="并发", expected_turn=0, request_key=key)
                return "created"
            except ConversationError as error:
                assert error.status_code == 409
                return "conflict"

    assert sorted(await asyncio.gather(reserve("a"), reserve("b"))) == ["conflict", "created"]
    async with rag_sessions() as db:
        rows = list((await db.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))).all())
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_generation_finish_allows_next_turn_and_visibility_is_atomic(rag_sessions, rag_users) -> None:
    """生成锁独立于评分，统一可见性同步所有既有任务。"""
    owner = rag_users[0]
    store = ConversationStore()
    conversation_id = await create_conversation(rag_sessions, owner)
    async with rag_sessions() as db:
        first, _ = await store.reserve_turn(db, conversation_id, owner, prompt="一", expected_turn=0, request_key="one")
        await store.finish_generation(db, conversation_id, first.id, owner, status="completed")
        second, _ = await store.reserve_turn(db, conversation_id, owner, prompt="二", expected_turn=1, request_key="two")
        assert second.turn_index == 2
        await store.set_visibility(db, conversation_id, owner, "public")
        tasks = list((await db.scalars(select(EvaluationTask).where(EvaluationTask.conversation_id == conversation_id))).all())
        assert len(tasks) == 2 and all(task.visibility == "public" for task in tasks)


@pytest.mark.asyncio
async def test_stale_finish_cannot_unlock_new_generation(rag_sessions, rag_users) -> None:
    """旧轮次迟到的完成事件不得释放当前新轮次的锁。"""
    owner = rag_users[0]
    store = ConversationStore()
    conversation_id = await create_conversation(rag_sessions, owner)
    async with rag_sessions() as db:
        first, _ = await store.reserve_turn(db, conversation_id, owner, prompt="一", expected_turn=0, request_key="one")
        await store.finish_generation(db, conversation_id, first.id, owner, status="completed")
        await store.reserve_turn(db, conversation_id, owner, prompt="二", expected_turn=1, request_key="two")
        with pytest.raises(ConversationError):
            await store.finish_generation(db, conversation_id, first.id, owner, status="completed")


@pytest.mark.asyncio
async def test_configuration_must_not_persist_secrets(rag_sessions, rag_users) -> None:
    """配置快照拒绝秘密字段，避免历史接口泄露供应商凭据。"""
    async with rag_sessions() as db:
        with pytest.raises(ConversationError):
            await ConversationStore().create(db, rag_users[0], mode="chat", title="不应保存", visibility="private",
                config={"modelIds": [1], "nested": {"api_key": "test-secret"}})
