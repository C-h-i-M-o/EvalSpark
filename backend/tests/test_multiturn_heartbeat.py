"""隔离库验证生成心跳只更新仍归属当前请求的轮次。"""
from datetime import datetime, timedelta

import pytest

from app.models.conversation import Conversation
from app.services.multiturn.heartbeat import pulse_generation
from app.services.multiturn.store import ConversationStore


@pytest.mark.asyncio
async def test_heartbeat_is_fenced_by_owner_turn_and_generation_state(rag_sessions, rag_users) -> None:
    """旧轮次、错误作者和已结束任务不能延长当前会话存活时间。"""
    owner = rag_users[0]
    store = ConversationStore()
    async with rag_sessions() as db:
        conversation = await store.create(db, owner, mode="chat", title="心跳", visibility="private", config={"modelIds": [1]})
        identity = conversation.id
        turn, _ = await store.reserve_turn(db, identity, owner, prompt="第一轮", expected_turn=0, request_key="heartbeat-one")
        turn_id = turn.id
    now = datetime.utcnow().replace(microsecond=0) + timedelta(seconds=1)
    assert not await pulse_generation(rag_sessions, identity, turn_id, rag_users[1], now=now)
    assert await pulse_generation(rag_sessions, identity, turn_id, owner, now=now)
    async with rag_sessions() as db:
        assert (await db.get(Conversation, identity)).updated_at == now.replace(microsecond=0)
        await store.finish_generation(db, identity, turn_id, owner, status="completed")
    assert not await pulse_generation(rag_sessions, identity, turn_id, owner, now=now)
    async with rag_sessions() as db:
        second, _ = await store.reserve_turn(db, identity, owner, prompt="第二轮", expected_turn=1, request_key="heartbeat-two")
        second_id = second.id
    assert not await pulse_generation(rag_sessions, identity, turn_id, owner, now=now)
    assert await pulse_generation(rag_sessions, identity, second_id, owner, now=now)
    async with rag_sessions() as db:
        await store.finish_generation(db, identity, second_id, owner, status="interrupted")
