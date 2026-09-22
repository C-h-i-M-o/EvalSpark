"""隔离 MySQL 验证 RAG 预留轮次初始化的并发与所有者边界。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, KnowledgeChunk
from app.models.model_config import ModelConfig, ModelProvider
from app.models.response import ModelResponse
from app.models.token_usage import TokenUsageLog
from app.adapters.base import ModelReply, ModelUsage
from app.services.multiturn.catalog import knowledge_snapshot, model_identity
from app.services.multiturn.store import ConversationStore, ConversationError
from app.services.rag.clients import RagClientError, VectorMatch
from app.services.rag.evaluation_store import RagEvaluationStore
from app.services.rag.usage import model_snapshot, external_stage
from test_rag_evaluation import model


@pytest.mark.asyncio
async def test_reserved_rag_initialization_is_atomic_and_owner_only(rag_sessions, rag_users) -> None:
    """并发初始化共用同一任务且仅创建一次候选，公开读者不能初始化。"""
    owner, reader = rag_users[:2]
    async with rag_sessions() as db:
        provider = ModelProvider(name=f"rag-multiturn-{uuid4().hex}")
        db.add(provider)
        await db.flush()
        config = ModelConfig(provider_id=provider.id, model_name="test", display_name="多轮测试")
        library = KnowledgeBase(user_id=owner, name="多轮资料", status="ready", content_revision=1)
        db.add_all([config, library])
        await db.flush()
        document = KnowledgeDocument(user_id=owner, knowledge_base_id=library.id, original_name="test.txt",
            storage_key=f"test-{uuid4().hex}", media_type="text/plain", size_bytes=1,
            content_hash=uuid4().hex, status="ready", index_revision=1, chunk_count=1)
        db.add(document)
        await db.flush()
        chunk_id, document_id = uuid4().hex, document.id
        db.add(KnowledgeChunk(id=chunk_id, document_id=document.id, user_id=owner, knowledge_base_id=library.id,
            index_revision=1, chunk_index=0, text="资料原文", token_count=4, source_json={"kind": "text", "lineStart": 1, "lineEnd": 1}))
        await db.commit()
        candidate = model(config.id)
        knowledge = await knowledge_snapshot(db, library.id, owner)
        library_id = library.id
        conversation = await ConversationStore().create(db, owner, mode="rag", title="并发初始化",
            visibility="public", knowledge_snapshot=knowledge, config={
                "modelIds": [candidate.id], "enableThinking": False,
                "identities": {str(candidate.id): model_identity(candidate)},
                "models": [model_snapshot(candidate).model_dump(mode="json", by_alias=True)]})
        conversation_id = conversation.id
        turn, _ = await ConversationStore().reserve_turn(db, conversation_id, owner,
            prompt="继续", expected_turn=0, request_key="first")
        turn_id, task_id = turn.id, turn.task_id
    store = RagEvaluationStore(rag_sessions)
    with pytest.raises(ConversationError) as forbidden:
        await store.create(reader, library_id, "继续", [candidate], enable_thinking=False,
                           conversation_id=conversation_id, reserved_turn_id=turn_id)
    assert forbidden.value.status_code == 404

    async def initialize():
        """独立事务争用同一预留轮次。"""
        return await store.create(owner, library_id, "继续", [candidate], enable_thinking=False,
                                  conversation_id=conversation_id, reserved_turn_id=turn_id)

    results = await asyncio.gather(initialize(), initialize(), return_exceptions=True)
    assert sum(isinstance(result, RagClientError) for result in results) == 1
    successful = [result for result in results if isinstance(result, tuple)]
    assert len(successful) == 1 and successful[0][0].task_id == task_id
    async with rag_sessions() as db:
        responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id == task_id))).all()
        assert len(responses) == 1 and responses[0].status == "pending"
    context, prepared = successful[0]
    prepared[0].evidence = await store.read_evidence(context, [VectorMatch(chunk_id, document_id, 1, 0, 0.9)])
    await store.fix_snapshots(context, prepared)
    response_id = prepared[0].response_id
    await store.save_stage(context, response_id, external_stage("generate", candidate, None, pending=True), start=True)
    usage = external_stage("generate", candidate, ModelReply("资料原文 [S1]", ModelUsage(10, 5), 1))
    await store.save_stage(context, response_id, usage)
    await store.save_answer(context, prepared[0], "资料原文 [S1]", usage)
    await store.finalize_multiturn_response(context, response_id)
    await store.finalize_multiturn_response(context, response_id)
    async with rag_sessions() as db:
        logs = (await db.scalars(select(TokenUsageLog).where(TokenUsageLog.response_id == response_id))).all()
        assert len(logs) == 1 and logs[0].total_tokens == 15
        assert (await db.get(ModelResponse, response_id)).status == "success"
