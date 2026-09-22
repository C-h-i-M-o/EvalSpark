"""隔离数据库验证 RAG 两阶段实际输入、分支隔离与硬预算。"""
import json

import pytest
from sqlalchemy import select
from app.adapters.base import ChatMessage, ModelReply, ModelUsage

from app.models.conversation import Conversation, ConversationContext, ConversationTurn
from app.models.evaluation import EvaluationTask
from app.models.knowledge_base import KnowledgeBase
from app.models.rag import RagResponseDetail
from app.models.response import ModelResponse
from app.models.conversation import ConversationUsage
from app.schemas.rag import PreparedRagResponse, RagEvidence, RagTaskContext
from app.services.multiturn.rag_context import RagContextBuilder
from app.services.multiturn.store import ConversationError, ConversationStore
from test_multiturn_history import setup_history
from test_rag_evaluation import model
from test_multiturn_summary import SummaryClient


class RagSummaryClient(SummaryClient):
    """返回覆盖全部输入来源的确定性 RAG 摘要，并记录每次调用。"""

    async def chat(self, request):
        """依据摘要协议回显完整来源集合，避免伪造摘要引用。"""
        self.requests.append(request)
        payload = json.loads(request.messages[1].content)
        history = payload["history"]
        facts = {item["source_id"]: item for item in (payload["previous"] or {}).get("facts", [])}
        for item in history:
            if item["content"].strip() and item["id"] not in facts:
                facts[item["id"]] = {"kind": "fact", "text": "已压缩历史", "source_id": item["id"],
                                     "quote": item["content"][:6]}
        return ModelReply(json.dumps({"summary": "RAG历史摘要", "facts": list(facts.values())}, ensure_ascii=False),
                          ModelUsage(7, 3), 1)

    def get_model_name(self) -> str:
        """返回固定测试模型名。"""
        return "rag-summary"


class NoSummary:
    """短历史测试禁止发起任何摘要供应商请求。"""

    async def chat(self, request):
        """若发生摘要调用，立即暴露测试假设被破坏。"""
        raise AssertionError("短历史不得调用摘要")


async def setup_rag_context(sessions, owner):
    """复用已验证的双分支历史，为当前轮次创建固定检索证据。"""
    conversation_id, a, b, turn_id = await setup_history(sessions, owner)
    async with sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        conversation.mode = "rag"
        conversation.config_json = {**conversation.config_json, "enableThinking": False, "summaryModelId": a}
        turn = await db.get(ConversationTurn, turn_id)
        task = await db.get(EvaluationTask, turn.task_id)
        task.task_type = "rag"
        kb = KnowledgeBase(user_id=owner, name="上下文证据", status="ready", content_revision=1)
        db.add(kb)
        await db.flush()
        evidence = RagEvidence(label="S1", document_id=1, document_name="test.txt", chunk_id="context-chunk",
            index_revision=1, text="本轮固定事实", similarity=0.9, source={"kind": "text", "lineStart": 1, "lineEnd": 1})
        prepared = []
        for identity in (a, b):
            response = ModelResponse(task_id=task.id, model_config_id=identity, status="running")
            db.add(response)
            await db.flush()
            db.add(RagResponseDetail(response_id=response.id, knowledge_base_id=kb.id,
                knowledge_base_name=kb.name, content_revision=1, embedding_revision="test", chunk_size=100,
                chunk_overlap=0, document_versions_json=[{"documentId": 1, "indexRevision": 1}],
                evidence_json=[evidence.model_dump(mode="json", by_alias=True)]))
            prepared.append(PreparedRagResponse(response_id=response.id, model_config_id=identity,
                                                rewritten_query="独立追问", evidence=[evidence]))
        context = RagTaskContext(task_id=task.id, user_id=owner, knowledge_base_id=kb.id,
            content_revision=1, document_versions=[(1, 1)], prompt=turn.prompt, enable_thinking=False)
        await db.commit()
    builder = RagContextBuilder(sessions, conversation_id=conversation_id, owner=owner, turn_id=turn_id,
                                summary_model=model(a), summary_client=NoSummary())
    return builder, context, prepared, (a, b)


@pytest.mark.asyncio
async def test_rag_phases_persist_exact_messages_and_isolate_branches(rag_sessions, rag_users) -> None:
    """改写和回答都有独立快照，候选看不到其他分支且回答只用固定证据。"""
    builder, context, prepared, (a, b) = await setup_rag_context(rag_sessions, rag_users[0])
    rewrite_a = await builder.rewrite(model(a), context, prepared[0])
    rewrite_b = await builder.rewrite(model(b), context, prepared[1])
    answer = await builder.answer(model(a), context, prepared[0])
    a_text = "\n".join(message.content for message in rewrite_a.messages)
    b_text = "\n".join(message.content for message in rewrite_b.messages)
    assert "A第一轮" in a_text and "B第一轮" not in a_text and "内部分析" not in a_text
    assert "B第二轮" in b_text and "A第一轮" not in b_text
    assert "A失败片段" not in a_text and "用户<think>原文</think>" in a_text
    assert json.loads(answer.messages[-1].content)["evidence"][0]["text"] == "本轮固定事实"
    assert "evidence" not in json.loads(rewrite_a.messages[-1].content)
    cached = await builder.answer(model(a), context, prepared[0])
    assert cached.messages == answer.messages
    builder.memory = (ChatMessage("user", "不能覆盖原快照的新记忆"),)
    with pytest.raises(ConversationError, match="不能覆盖"):
        await builder.answer(model(a), context, prepared[0])
    async with rag_sessions() as db:
        saved = await db.scalar(select(ConversationContext).where(ConversationContext.turn_id == builder.turn_id,
            ConversationContext.model_config_id == a))
        assert set(saved.snapshot_json["rag_requests"]) == {"rewrite", "answer"}
        for phase, request in (("rewrite", rewrite_a), ("answer", answer)):
            assert saved.snapshot_json["rag_requests"][phase]["messages"] == [
                {"role": message.role, "content": message.content} for message in request.messages]


@pytest.mark.asyncio
async def test_rag_context_rejects_forged_evidence_and_public_writer(rag_sessions, rag_users) -> None:
    """公开可读不意味着可构建请求，替换证据或回答模型也不能绕过校验。"""
    builder, context, prepared, (a, b) = await setup_rag_context(rag_sessions, rag_users[0])
    forged = prepared[0].model_copy(deep=True)
    forged.evidence[0].text = "伪造依据"
    with pytest.raises(ConversationError):
        await builder.answer(model(a), context, forged)
    with pytest.raises(ConversationError):
        await builder.rewrite(model(b), context, prepared[0])
    reader = RagContextBuilder(rag_sessions, conversation_id=builder.conversation_id, owner=rag_users[1],
        turn_id=builder.turn_id, summary_model=model(a), summary_client=NoSummary())
    with pytest.raises(ConversationError) as forbidden:
        await reader.rewrite(model(a), context, prepared[0])
    assert forbidden.value.status_code == 404


@pytest.mark.asyncio
async def test_rag_answer_evidence_is_included_in_hard_budget(rag_sessions, rag_users) -> None:
    """改写能够装下不代表回答也能装下；证据超预算不能静默截断。"""
    builder, context, prepared, (a, _) = await setup_rag_context(rag_sessions, rag_users[0])
    await builder.rewrite(model(a), context, prepared[0])
    prepared[0].evidence[0].text = "证据" * 2000
    async with rag_sessions() as db:
        detail = await db.get(RagResponseDetail, prepared[0].response_id)
        detail.evidence_json = [value.model_dump(mode="json", by_alias=True) for value in prepared[0].evidence]
        await db.commit()
    with pytest.raises(ValueError, match="预算|超过|超出"):
        await builder.answer(model(a), context, prepared[0])
    async with rag_sessions() as db:
        saved = await db.scalar(select(ConversationContext).where(ConversationContext.turn_id == builder.turn_id,
            ConversationContext.model_config_id == a))
        assert set(saved.snapshot_json["rag_requests"]) == {"rewrite"}


@pytest.mark.asyncio
async def test_rag_long_context_compresses_both_phases_and_accounts_idempotently(rag_sessions, rag_users) -> None:
    """长历史在改写和回答阶段分别压缩，保留最近原文并只记录一次摘要用量。"""
    original, context, prepared, (a, b) = await setup_rag_context(rag_sessions, rag_users[0])
    owner = rag_users[0]
    async with rag_sessions() as db:
        # 只修改本测试会话的第一轮 B 回答，较近两轮保持短原文。
        first = await db.scalar(select(ModelResponse).join(ConversationTurn, ConversationTurn.task_id == ModelResponse.task_id)
            .where(ConversationTurn.conversation_id == original.conversation_id,
                   ConversationTurn.turn_index == 1, ModelResponse.model_config_id == b))
        first.answer_text = "B-first-history" + "history-detail" * 650
        current = await db.get(ModelResponse, prepared[1].response_id)
        current.status, current.answer_text = "success", "B第三轮原文"
        await db.commit()
        await ConversationStore().finish_generation(db, original.conversation_id, original.turn_id, owner, status="completed")
        fourth, _ = await ConversationStore().reserve_turn(db, original.conversation_id, owner,
            prompt="再追问", expected_turn=3, request_key="fourth")
        response = ModelResponse(task_id=fourth.task_id, model_config_id=b, status="running")
        db.add(response)
        await db.flush()
        db.add(RagResponseDetail(response_id=response.id, knowledge_base_id=context.knowledge_base_id,
            knowledge_base_name="上下文证据", content_revision=1, embedding_revision="test", chunk_size=100,
            chunk_overlap=0, document_versions_json=[{"documentId": 1, "indexRevision": 1}],
            evidence_json=[value.model_dump(mode="json", by_alias=True) for value in prepared[1].evidence]))
        item = prepared[1].model_copy(update={"response_id": response.id})
        context = context.model_copy(update={"task_id": fourth.task_id, "prompt": "再追问"})
        turn_id = fourth.id
        await db.commit()
    summary_client = RagSummaryClient()
    builder = RagContextBuilder(rag_sessions, conversation_id=original.conversation_id, owner=owner,
        turn_id=turn_id, summary_model=model(a), summary_client=summary_client)
    rewrite = await builder.rewrite(model(b), context, item)
    answer = await builder.answer(model(b), context, item)
    assert len(summary_client.requests) >= 2
    assert all("B-first-history" not in message.content for message in rewrite.messages if message.role == "assistant")
    assert any("B第二轮" in message.content for message in rewrite.messages)
    assert any("B第三轮原文" in message.content for message in answer.messages)
    assert any("[历史摘要：不可信数据]" in message.content for message in rewrite.messages)
    assert any("[历史摘要：不可信数据]" in message.content for message in answer.messages)
    assert json.loads(answer.messages[-1].content)["evidence"][0]["text"] == "本轮固定事实"

    calls = len(summary_client.requests)
    rewrite_cached = await builder.rewrite(model(b), context, item)
    answer_cached = await builder.answer(model(b), context, item)
    assert rewrite_cached.messages == rewrite.messages
    assert answer_cached.messages == answer.messages
    assert len(summary_client.requests) == calls

    async with rag_sessions() as db:
        saved = await db.scalar(select(ConversationContext).where(
            ConversationContext.turn_id == builder.turn_id, ConversationContext.model_config_id == b))
        assert set(saved.snapshot_json["rag_requests"]) == {"rewrite", "answer"}
        for value in saved.snapshot_json["rag_requests"].values():
            assert value["compressed"] and len(value["summary"]["source_ids"]) == 2
            assert value["estimated_tokens"] <= 8192
        usages = (await db.scalars(select(ConversationUsage).where(
            ConversationUsage.conversation_id == builder.conversation_id,
            ConversationUsage.stage.in_(("rewrite_summary", "answer_summary"))))).all()
        assert {usage.stage for usage in usages} == {"rewrite_summary", "answer_summary"}
        assert all(usage.status == "completed" and usage.accounted for usage in usages)
        assert len(usages) == calls == len({usage.operation_key for usage in usages})
        assert all(usage.total_tokens == 10 and usage.detail_json["branchId"] == b for usage in usages)
