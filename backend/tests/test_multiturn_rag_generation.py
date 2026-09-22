"""隔离 MySQL 和确定性模型验证 RAG 连续生成，不调用真实供应商。"""
import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelStreamEvent, ModelUsage
from app.models.conversation import Conversation, ConversationTurn, ConversationUsage
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.response import ModelResponse
from app.models.token_usage import TokenUsageLog
from app.schemas.conversation import TurnCreate
from app.services.multiturn.catalog import knowledge_snapshot
from app.services.multiturn.rag_generation import RagConversationGenerator
from app.services.rag.clients import VectorMatch
from app.services.rag.evaluation import RagEvaluationRunner
from test_multiturn_generation import setup


class ContextClient:
    """记录实际改写及回答上下文，并支持模拟流式中断。"""

    def __init__(self, identity, judge_id, captured, *, wait=False):
        """按配置标识区分共享要求提取与候选回答。"""
        self.identity, self.judge_id, self.captured, self.wait = identity, judge_id, captured, wait

    def get_model_name(self):
        """返回固定摘要模型标识。"""
        return f"model-{self.identity}"

    async def chat(self, request):
        """摘要模型仅处理本测试的空要求，候选负责独立检索改写。"""
        if self.identity == self.judge_id:
            return ModelReply('{"requirements": []}', ModelUsage(5, 5), 1)
        self.captured.setdefault((self.identity, "rewrite"), []).append(request)
        return ModelReply("独立查询", ModelUsage(3, 2), 1)

    async def stream_chat(self, request):
        """流式回答带固定证据引用，等待模式用于验证关闭时收尾。"""
        self.captured.setdefault((self.identity, "answer"), []).append(request)
        answer = f"branch-{self.identity} [S1]"
        yield ModelStreamEvent(delta=answer)
        if self.wait:
            await asyncio.Event().wait()
        yield ModelStreamEvent(delta="", reply=ModelReply(answer, ModelUsage(10, 5), 1))


class FixedEmbedding:
    """固定本地向量，避免网络和模型权重。"""

    def input_token_count(self, query, kind):
        """本地检索用量保持可观测但不计入外部模型额度。"""
        return 4

    async def embed(self, queries, kind):
        """为每个查询返回可由假向量库读取的确定性向量。"""
        return [[1.0] for _ in queries]


class FixedVectors:
    """只允许查询本测试用户与知识库，返回实际已存储的片段。"""

    def __init__(self, owner, knowledge_id, document_id, chunk_id):
        """保存期望权限范围与真实片段标识。"""
        self.owner, self.knowledge_id, self.document_id, self.chunk_id = owner, knowledge_id, document_id, chunk_id

    async def search(self, owner, knowledge_id, versions, vector, *, limit):
        """检索参数必须带当前用户及冻结文档版本。"""
        assert owner == self.owner and knowledge_id == self.knowledge_id and versions == [(self.document_id, 1)]
        return [VectorMatch(self.chunk_id, self.document_id, 1, 0, 0.9)]


async def setup_generator(sessions, owner, monkeypatch, *, wait=False):
    """建立真实会话/知识片段，仅替换模型、Embedding 与向量服务调用。"""
    monkeypatch.setattr("app.services.multiturn.turn_scoring.publish_assessment", lambda job_id: True)
    conversation_id, identities = await setup(sessions, owner, monkeypatch, with_judge=True)
    async with sessions() as db:
        kb = KnowledgeBase(user_id=owner, name="多轮生成资料", status="ready", content_revision=1)
        db.add(kb)
        await db.flush()
        document = KnowledgeDocument(user_id=owner, knowledge_base_id=kb.id, original_name="test.txt",
            storage_key=uuid4().hex, media_type="text/plain", size_bytes=4, content_hash=uuid4().hex,
            status="ready", index_revision=1, chunk_count=1)
        db.add(document)
        await db.flush()
        chunk_id = uuid4().hex
        db.add(KnowledgeChunk(id=chunk_id, document_id=document.id, knowledge_base_id=kb.id, user_id=owner,
            index_revision=1, chunk_index=0, text="固定资料", token_count=4,
            source_json={"kind": "text", "lineStart": 1, "lineEnd": 1}))
        await db.commit()
        conversation = await db.get(Conversation, conversation_id)
        conversation.mode = "rag"
        conversation.config_json = {**conversation.config_json, "knowledgeBaseId": kb.id, "scoreVersion": "rag-multiturn-v1"}
        conversation.knowledge_snapshot_json = await knowledge_snapshot(db, kb.id, owner)
        await db.commit()
        vectors = FixedVectors(owner, kb.id, document.id, chunk_id)
    captured = {}

    def factory(model):
        """每个分支创建独立测试客户端，共享只用于断言的调用记录。"""
        return ContextClient(model.id, identities[2], captured, wait=wait)

    def runner(store):
        """RAG 持久化保持真实，外部模型和检索确定性替换。"""
        return RagEvaluationRunner(store, embedding=FixedEmbedding(), vectors=vectors, client_factory=factory)

    return RagConversationGenerator(sessions, client_factory=factory, runner_factory=runner), conversation_id, identities, captured


@pytest.mark.asyncio
async def test_rag_two_rounds_replay_and_independent_context(rag_sessions, rag_users, monkeypatch) -> None:
    """两轮均完成检索与生成，重放不调用模型，历史与费用不串分支。"""
    generator, conversation_id, identities, captured = await setup_generator(rag_sessions, rag_users[0], monkeypatch)
    first = TurnCreate(prompt="第一轮问题", expectedTurn=0, requestKey="first")
    events = [event async for event in generator.stream(conversation_id, rag_users[0], first)]
    assert events[-1]["type"] == "turn_completed"
    assert sum(event["type"] == "answer_completed" for event in events) == 2
    for identity in identities[:2]:
        statuses = [event for event in events if event["type"] == "context_ready" and event["modelConfigId"] == identity]
        assert [event["phase"] for event in statuses] == ["rewrite", "answer"]
        assert all(event["historyThroughTurn"] == 0 and type(event["estimatedTokens"]) is int for event in statuses)
        assert all(set(event) == {"type", "modelConfigId", "phase", "compressed", "estimatedTokens", "historyThroughTurn"} for event in statuses)
        first_delta = next(index for index, event in enumerate(events) if event["type"] == "delta" and event["modelConfigId"] == identity)
        assert events.index(statuses[1]) < first_delta
    replay = [event async for event in generator.stream(conversation_id, rag_users[0], first)]
    assert len(replay) == 1 and replay[0]["replayed"] is True
    second = [event async for event in generator.stream(conversation_id, rag_users[0],
        TurnCreate(prompt="接着解释", expectedTurn=1, requestKey="second"))]
    assert second[-1]["type"] == "turn_completed"
    assert all(event["historyThroughTurn"] == 1 for event in second if event["type"] == "context_ready")
    for identity, other in ((identities[0], identities[1]), (identities[1], identities[0])):
        for phase in ("rewrite", "answer"):
            assert len(captured[identity, phase]) == 2
            text = "\n".join(message.content for message in captured[identity, phase][1].messages)
            assert f"branch-{identity}" in text and f"branch-{other}" not in text
    async with rag_sessions() as db:
        turns = (await db.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))).all()
        responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id.in_([turn.task_id for turn in turns])))).all()
        assert len(responses) == 4 and all(response.status == "success" for response in responses)
        logs = (await db.scalars(select(TokenUsageLog).where(TokenUsageLog.response_id.in_([response.id for response in responses])))).all()
        assert len(logs) == 4 and all(log.total_tokens == 20 for log in logs)
        stages = (await db.scalars(select(ConversationUsage).where(ConversationUsage.conversation_id == conversation_id))).all()
        assert len(stages) == 2 and all(stage.stage == "requirements" for stage in stages)


@pytest.mark.asyncio
async def test_history_reference_enters_both_requests_and_new_score_snapshot(rag_sessions, rag_users, monkeypatch) -> None:
    """显式旧引用进入两阶段实际请求，新评分仍使用本轮固定证据。"""
    import json
    from app.models.conversation import ConversationAssessment
    generator, conversation_id, identities, captured = await setup_generator(rag_sessions, rag_users[0], monkeypatch)
    _ = [event async for event in generator.stream(conversation_id, rag_users[0],
        TurnCreate(prompt="先回答", expectedTurn=0, requestKey="history-first"))]
    events = [event async for event in generator.stream(conversation_id, rag_users[0],
        TurnCreate(prompt="解释[T1:S1]", expectedTurn=1, requestKey="history-second"))]
    assert events[-1]["type"] == "turn_completed"
    source_ids = []
    for identity in identities[:2]:
        for phase in ("rewrite", "answer"):
            request = captured[identity, phase][1]
            data = json.loads(request.messages[-1].content)
            reference = data["historicalReferences"][0]
            assert reference["evidence"]["text"] == "固定资料"
            assert reference["turn"] == 1
            if phase == "answer":
                assert reference["currentLabel"] == "S1"
                assert data["evidence"][0]["text"] == "固定资料"
                source_ids.append(reference["sourceId"])
    assert len(set(source_ids)) == 2
    async with rag_sessions() as db:
        jobs = (await db.scalars(select(ConversationAssessment).where(ConversationAssessment.conversation_id == conversation_id,
            ConversationAssessment.through_turn == 2))).all()
        assert len(jobs) == 2
        assert all(job.input_json["packet"]["rag"]["evidence"][0]["text"] == "固定资料" for job in jobs)
        assert all(job.input_json["packet"]["rag"]["evidence"][0]["historical_references"] == [
            {"reference": "T1:S1", "source_id": source_ids[identities.index(job.model_config_id)]}
        ] for job in jobs)


@pytest.mark.asyncio
async def test_rag_close_cancels_producer_and_releases_turn(rag_sessions, rag_users, monkeypatch) -> None:
    """收到部分文本后断连，生成任务停止且会话锁释放，未知费用不当作成功回答。"""
    generator, conversation_id, _, _ = await setup_generator(rag_sessions, rag_users[0], monkeypatch, wait=True)
    stream = generator.stream(conversation_id, rag_users[0], TurnCreate(prompt="开始", expectedTurn=0, requestKey="cancel"))
    async for event in stream:
        if event["type"] == "delta":
            break
    await asyncio.wait_for(stream.aclose(), 10)
    async with rag_sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))
        assert conversation.generation_status == "idle" and turn.generation_status == "interrupted"
        responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id == turn.task_id))).all()
        assert all(response.status == "failed" for response in responses)


@pytest.mark.asyncio
async def test_zero_new_hits_uses_explicit_history_only(rag_sessions, rag_users, monkeypatch) -> None:
    """新检索为空时，显式引用仍可生成；未请求历史资料时不能凭旧回答生成。"""
    generator, conversation_id, identities, captured = await setup_generator(rag_sessions, rag_users[0], monkeypatch)
    _ = [event async for event in generator.stream(conversation_id, rag_users[0],
        TurnCreate(prompt="先回答", expectedTurn=0, requestKey="zero-first"))]

    async def no_hits(*args, **kwargs):
        """第二轮起没有新检索命中，历史资料仍从真实数据库校验。"""
        return []

    monkeypatch.setattr(FixedVectors, "search", no_hits)
    events = [event async for event in generator.stream(conversation_id, rag_users[0],
        TurnCreate(prompt="解释[T1:S1]", expectedTurn=1, requestKey="zero-history"))]
    assert sum(event["type"] == "answer_completed" for event in events) == 2
    assert any(event["type"] == "assessments_queued" for event in events)
    assert all(len(captured[identity, "answer"]) == 2 for identity in identities[:2])
    _ = [event async for event in generator.stream(conversation_id, rag_users[0],
        TurnCreate(prompt="全新问题", expectedTurn=2, requestKey="zero-no-history"))]
    assert all(len(captured[identity, "answer"]) == 2 for identity in identities[:2])
    async with rag_sessions() as db:
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id,
            ConversationTurn.turn_index == 3))
        responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id == turn.task_id))).all()
        assert len(responses) == 2 and all(item.status == "failed" for item in responses)

@pytest.mark.asyncio
async def test_legacy_recovery_does_not_interrupt_live_multiturn(rag_sessions, rag_users, monkeypatch) -> None:
    """旧任务创建时间扫描不得结束仍在生成的多轮任务。"""
    from app.models.evaluation import EvaluationTask
    owner = rag_users[0]
    generator, conversation_id, _, _ = await setup_generator(rag_sessions, owner, monkeypatch, wait=True)
    stream = generator.stream(conversation_id, owner, TurnCreate(prompt="长任务", expectedTurn=0, requestKey="legacy-recovery"))
    try:
        async for event in stream:
            if event["type"] == "delta":
                break
        assert any(task.get_name().startswith(f"conversation-heartbeat:{conversation_id}:") for task in asyncio.all_tasks())
        async with rag_sessions() as db:
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))
            task_id = turn.task_id
            task = await db.get(EvaluationTask, task_id)
            task.created_at = datetime.utcnow() - timedelta(days=1)
            await db.commit()
        await generator.rag_store.recover_interrupted()
        async with rag_sessions() as db:
            assert (await db.get(EvaluationTask, task_id)).status == "pending"
            assert (await db.get(Conversation, conversation_id)).generation_status == "generating"
    finally:
        await stream.aclose()
    assert not any(task.get_name().startswith(f"conversation-heartbeat:{conversation_id}:") for task in asyncio.all_tasks())
