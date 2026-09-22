"""真实隔离 MySQL 验证 RAG 多轮 HTTP 分发和响应头前权限边界。"""
import json

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services.multiturn.generation import ConversationGenerator
from test_multiturn_rag_generation import setup_generator


@pytest.mark.asyncio
async def test_rag_http_history_replay_and_preflight(rag_sessions, rag_users, monkeypatch) -> None:
    """同一接口完成两轮及重放，拒绝公开读者续聊、错误引用和知识版本变化。"""
    owner = rag_users[0]
    generator, conversation_id, identities, captured = await setup_generator(rag_sessions, owner, monkeypatch)
    monkeypatch.setattr("app.api.v1.conversations.rag_conversation_generator", generator, raising=False)
    monkeypatch.setattr("app.api.v1.conversations.conversation_generator", ConversationGenerator(rag_sessions))
    actor = [owner]

    async def database():
        """只向当前测试固定数据库提供请求事务。"""
        async with rag_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: User(id=actor[0], username="隔离测试", role="user", status="active")
    path = f"/api/evaluation/conversations/{conversation_id}/turns/stream"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for index, prompt in enumerate(("解释资料", "解释[T1:S1]")):
                payload = {"prompt": prompt, "expectedTurn": index, "requestKey": f"http-{index}"}
                response = await client.post(path, json=payload)
                assert response.status_code == 200, response.text
                events = [json.loads(line) for line in response.text.splitlines()]
                assert events[0]["type"] == "turn_started" and events[-1]["type"] == "turn_completed"
                assert any(event["type"] == "rag_retrieval" for event in events)
                assert any(event["type"] == "assessments_queued" for event in events)
            replay = await client.post(path, json=payload)
            assert replay.status_code == 200 and len(replay.text.splitlines()) == 1
            assert json.loads(replay.text)["replayed"] is True
            assert all(len(captured[identity, "answer"]) == 2 for identity in identities[:2])
            invalid = await client.post(path, json={"prompt": "解释[T99:S1]", "expectedTurn": 2, "requestKey": "invalid"})
            assert invalid.status_code == 422 and "application/json" in invalid.headers["content-type"]
            async with rag_sessions() as db:
                conversation = await db.get(Conversation, conversation_id)
                conversation.visibility = "public"
                knowledge_id = conversation.config_json["knowledgeBaseId"]
                await db.commit()
            actor[0] = rag_users[1]
            denied = await client.post(path, json={"prompt": "继续", "expectedTurn": 2, "requestKey": "other"})
            assert denied.status_code == 404
            actor[0] = owner
            async with rag_sessions() as db:
                knowledge = await db.get(KnowledgeBase, knowledge_id)
                knowledge.content_revision += 1
                await db.commit()
            changed = await client.post(path, json={"prompt": "继续", "expectedTurn": 2, "requestKey": "changed"})
            assert changed.status_code == 409 and changed.json()["detail"]["code"] == "knowledge_base_changed"
            assert all(len(captured[identity, "answer"]) == 2 for identity in identities[:2])
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
