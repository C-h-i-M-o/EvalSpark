"""隔离 MySQL 验证普通多轮并发生成、独立上下文与流关闭收尾。"""
import asyncio
import json
from decimal import Decimal
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelStreamEvent, ModelUsage
from app.models.conversation import Conversation, ConversationTurn, ConversationUsage, ConversationAssessment, ConversationRequirement
from app.models.model_config import ModelConfig, ModelProvider
from app.models.response import ModelResponse
from app.schemas.conversation import ConversationCreate, TurnCreate
from app.services.multiturn.catalog import ConversationCatalog
from app.services.multiturn.generation import ConversationGenerator
from test_rag_evaluation import model


async def setup(sessions, owner, monkeypatch, with_judge=False):
    """建立实际模型外键，供应商解析使用不访问网络的测试配置。"""
    async with sessions() as db:
        provider = ModelProvider(name=f"generate-{uuid4().hex}")
        db.add(provider)
        await db.flush()
        names = ("A", "B", "Judge") if with_judge else ("A", "B")
        records = [ModelConfig(provider_id=provider.id, model_name=name, display_name=name) for name in names]
        db.add_all(records)
        await db.commit()
        ids = [record.id for record in records]
        models = [replace(model(identity), model_name=f"model-{identity}") for identity in ids]
        monkeypatch.setattr("app.services.multiturn.catalog.model_config_service.resolve_runtime_models",
                            AsyncMock(return_value=models))
        monkeypatch.setattr("app.services.multiturn.runtime.model_config_service.resolve_runtime_model",
                            AsyncMock(side_effect=lambda db, identity: next(item for item in models if item.id == identity)))
        conversation = await ConversationCatalog().create(db, owner, ConversationCreate(modelIds=ids[:2],
            judgeModelId=ids[2] if with_judge else None))
        return conversation.id, ids


class StreamingClient:
    """捕获上下文，以固定完成消息模拟真实流式适配器。"""

    def __init__(self, identity, requests, *, fail=False, wait=False):
        """注入失败或长时间生成以验证隔离及取消。"""
        self.identity, self.requests, self.fail, self.wait = identity, requests, fail, wait

    def get_model_name(self):
        """返回要求提取使用的测试模型名。"""
        return f"model-{self.identity}"

    async def chat(self, request):
        """测试普通无显式约束问题，要求提取返回空集合并报告实际用量。"""
        return ModelReply('{"requirements": []}', ModelUsage(5, 5), 1)

    async def stream_chat(self, request):
        """先返回 delta，再提供完整 reply 或模拟未完成断连。"""
        self.requests.setdefault(self.identity, []).append(request)
        yield ModelStreamEvent(delta=f"answer-{self.identity}")
        if self.wait:
            await asyncio.Event().wait()
        if self.fail:
            raise RuntimeError("模拟供应商失败")
        yield ModelStreamEvent(delta="", reply=ModelReply(f"answer-{self.identity}", ModelUsage(10, 5), 1))


@pytest.mark.asyncio
async def test_two_rounds_use_independent_history_and_replay_does_not_call(rag_sessions, rag_users, monkeypatch):
    """同题并发两轮，各分支只能看到自己的上一轮答案；请求重放不付费。"""
    owner = rag_users[0]
    conversation_id, ids = await setup(rag_sessions, owner, monkeypatch)
    requests = {}
    service = ConversationGenerator(rag_sessions, client_factory=lambda runtime: StreamingClient(runtime.id, requests))
    first = TurnCreate(prompt="第一问", expectedTurn=0, requestKey="one")
    events = [event async for event in service.stream(conversation_id, owner, first)]
    assert events[-1]["type"] == "turn_completed"
    second = TurnCreate(prompt="继续", expectedTurn=1, requestKey="two")
    events = [event async for event in service.stream(conversation_id, owner, second)]
    assert len([event for event in events if event["type"] == "answer_completed"]) == 2
    for identity in ids:
        messages = requests[identity][1].messages
        assert [message.content for message in messages[1:]] == ["第一问", f"answer-{identity}", "继续"]
    replay = [event async for event in service.stream(conversation_id, owner, second)]
    assert replay[0]["replayed"] and len(replay) == 1
    assert all(len(items) == 2 for items in requests.values())
    async with rag_sessions() as db:
        usage = list((await db.scalars(select(ConversationUsage).where(ConversationUsage.conversation_id == conversation_id))).all())
        assert len(usage) == 6 and all(item.accounted for item in usage)


@pytest.mark.asyncio
async def test_one_failed_model_does_not_discard_other_answer(rag_sessions, rag_users, monkeypatch):
    """失败仅影响所属模型，成功结果正常持久化。"""
    owner = rag_users[0]
    conversation_id, ids = await setup(rag_sessions, owner, monkeypatch)
    service = ConversationGenerator(rag_sessions, client_factory=lambda runtime: StreamingClient(runtime.id, {}, fail=runtime.id == ids[0]))
    events = [event async for event in service.stream(conversation_id, owner, TurnCreate(prompt="问题", expectedTurn=0, requestKey="one"))]
    assert sorted(event["status"] for event in events if event["type"] == "answer_completed") == ["failed", "success"]
    async with rag_sessions() as db:
        assert (await db.get(Conversation, conversation_id)).generation_status == "idle"


@pytest.mark.asyncio
async def test_stream_close_cancels_branches_and_unlocks_turn(rag_sessions, rag_users, monkeypatch):
    """用户断开流后取消供应商等待，失败残片不进入后续历史。"""
    owner = rag_users[0]
    conversation_id, _ = await setup(rag_sessions, owner, monkeypatch)
    service = ConversationGenerator(rag_sessions, client_factory=lambda runtime: StreamingClient(runtime.id, {}, wait=True))
    stream = service.stream(conversation_id, owner, TurnCreate(prompt="问题", expectedTurn=0, requestKey="one"))
    async for event in stream:
        if event["type"] == "delta":
            break
    await asyncio.wait_for(stream.aclose(), timeout=5)
    async with rag_sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation.generation_status == "idle"
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))
        assert turn.generation_status == "interrupted"
        responses = list((await db.scalars(select(ModelResponse).where(ModelResponse.task_id == turn.task_id))).all())
        assert all(item.status == "failed" and not item.answer_text for item in responses)


@pytest.mark.asyncio
async def test_http_two_rounds_with_real_persistence(rag_sessions, rag_users, monkeypatch):
    """HTTP NDJSON 到真实隔离 MySQL 两轮完成，模型请求保持分支历史隔离。"""
    from app.main import app
    from app.api.dependencies import get_current_user
    from app.models.user import User
    from app.db.session import get_db
    owner = rag_users[0]
    conversation_id, ids = await setup(rag_sessions, owner, monkeypatch)
    requests = {}
    monkeypatch.setattr("app.api.v1.conversations.conversation_generator", ConversationGenerator(
        rag_sessions, client_factory=lambda runtime: StreamingClient(runtime.id, requests)))
    app.dependency_overrides[get_current_user] = lambda: User(id=owner, username="隔离测试", role="user", status="active")

    async def database():
        """HTTP 分发和生成使用同一隔离数据库，各自保留短事务。"""
        async with rag_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for index in range(2):
                response = await client.post(f"/api/evaluation/conversations/{conversation_id}/turns/stream",
                    json={"prompt": f"问题{index}", "expectedTurn": index, "requestKey": f"request{index}"})
                assert response.status_code == 200 and "turn_completed" in response.text
            for identity in ids:
                assert requests[identity][1].messages[-2].content == f"answer-{identity}"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_generation_extracts_requirements_and_queues_grounded_scoring(rag_sessions, rag_users, monkeypatch):
    """从要求提取到成功回答评分作业自动入库，随后经 Worker 入口得到暂定分。"""
    from app.services.multiturn.dispatch import run_assessment_job
    owner = rag_users[0]
    conversation_id, ids = await setup(rag_sessions, owner, monkeypatch, with_judge=True)
    published = []
    monkeypatch.setattr("app.services.multiturn.turn_scoring.publish_assessment", lambda identity: published.append(identity) or True)

    class ReviewingClient(StreamingClient):
        """分别识别要求提取及评分请求，返回带原文来源的固定结果。"""

        async def chat(self, request):
            """使用接口实际传入的固定 ID，验证整个装配与解析流程。"""
            data = json.loads(request.messages[1].content)
            if "current" in data:
                answer = {"requirements": [{"text": "请回答", "quote": "请回答", "scope": "turn",
                    "critical": False, "ambiguous": False, "supersedes": []}]}
            else:
                answer = {"items": [{"id": check["id"], "applicability": "applicable", "rating": 4,
                    "passed": True if check["critical"] else None, "reason": "测试判定", "evidence": [
                        {"source_id": "answer", "quote": data["answer"]}]} for check in data["checks"]]}
            return ModelReply(json.dumps(answer, ensure_ascii=False), ModelUsage(5, 5), 1)

        def estimate_cost(self, usage):
            """模拟评审费用，真实用量仍由适配器返回。"""
            return Decimal("0.01")

    factory = lambda runtime: ReviewingClient(runtime.id, {})
    service = ConversationGenerator(rag_sessions, client_factory=factory)
    events = [event async for event in service.stream(conversation_id, owner,
        TurnCreate(prompt="请回答", expectedTurn=0, requestKey="one"))]
    queued = next(event for event in events if event["type"] == "assessments_queued")
    assert queued["assessmentIds"] == published and len(published) == 2
    async with rag_sessions() as db:
        requirements = list((await db.scalars(select(ConversationRequirement).where(
            ConversationRequirement.conversation_id == conversation_id))).all())
        assert len(requirements) == 1
        for identity in published:
            job = await db.get(ConversationAssessment, identity)
            assert job.response_id is not None and job.status == "queued"
            assert any(check["requirement_id"] == requirements[0].requirement_key for check in job.input_json["packet"]["checks"])
    monkeypatch.setattr("app.services.multiturn.dispatch.create_client", factory)
    for identity in published:
        assert await run_assessment_job(rag_sessions, identity)
    async with rag_sessions() as db:
        for identity in published:
            job = await db.get(ConversationAssessment, identity)
            assert job.status == "provisional" and Decimal(job.result_json["score"]["final"]) == 10
