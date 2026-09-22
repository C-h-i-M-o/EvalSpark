"""隔离 MySQL 验证生成失活收尾和恢复后的写入边界。"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelUsage
from app.models.conversation import Conversation, ConversationTurn, ConversationUsage
from app.models.evaluation import EvaluationTask
from app.models.response import ModelResponse
from app.schemas.conversation import TurnCreate
from app.services.multiturn.generation import ConversationGenerator
from app.services.multiturn.generation_recovery import recover_generations
from app.services.multiturn.history import load_branch_history
from app.services.multiturn.store import ConversationError, ConversationStore
from test_multiturn_generation import setup
from test_rag_evaluation import model


@pytest.mark.asyncio
async def test_stale_generation_settles_once_preserves_success_and_blocks_late_writes(rag_sessions, rag_users, monkeypatch) -> None:
    """失活回收不重发供应商，已完成分支和费用保留，旧回调不能复活回答。"""
    owner = rag_users[0]
    identity, models = await setup(rag_sessions, owner, monkeypatch)
    store = ConversationStore()
    async with rag_sessions() as db:
        turn, _ = await store.reserve_turn(db, identity, owner, prompt="恢复测试", expected_turn=0, request_key="crash")
        turn_id, task_id = turn.id, turn.task_id
        good = ModelResponse(task_id=task_id, model_config_id=models[0], status="success", answer_text="已完成回答")
        pending = ModelResponse(task_id=task_id, model_config_id=models[1], status="pending")
        db.add_all([good, pending])
        await db.flush()
        pending_id = pending.id
        known = ConversationUsage(conversation_id=identity, user_id=owner, operation_key=f"summary:{turn_id}:test",
            stage="summary", status="completed", currency="CNY", total_tokens=15, cost=Decimal(".01"), detail_json={"turnId": turn_id})
        unknown = ConversationUsage(conversation_id=identity, user_id=owner, operation_key=f"generate:{pending_id}",
            stage="generate", status="pending", currency="CNY", detail_json={"responseId": pending_id})
        db.add_all([known, unknown])
        conversation = await db.get(Conversation, identity)
        conversation.updated_at = datetime.utcnow() - timedelta(hours=5)
        await db.commit()
        known_id, unknown_id = known.id, unknown.id
    await recover_generations(rag_sessions, limit=100)
    await recover_generations(rag_sessions, limit=100)
    generator = ConversationGenerator(rag_sessions)
    await generator._save(identity, owner, pending_id, model(models[1]), ModelReply("迟到回答", ModelUsage(7, 3), 1), True, None)
    async with rag_sessions() as db:
        assert (await db.get(Conversation, identity)).generation_status == "idle"
        assert (await db.get(ConversationTurn, turn_id)).generation_status == "interrupted"
        assert (await db.get(EvaluationTask, task_id)).status == "interrupted"
        response = await db.get(ModelResponse, pending_id)
        assert response.status == "failed" and response.answer_text == ""
        assert (await db.get(ConversationUsage, known_id)).accounted
        unknown = await db.get(ConversationUsage, unknown_id)
        assert unknown.status == "unknown" and unknown.total_tokens is None and not unknown.accounted
        history = await load_branch_history(db, identity, owner, models[0], before_turn=2)
        assert [item.message.content for item in history] == ["恢复测试", "已完成回答"]
        assert not await load_branch_history(db, identity, owner, models[1], before_turn=2)
        with pytest.raises(ConversationError):
            await store.require_generating(db, identity, turn_id, owner)
        await db.rollback()
        replay, created = await store.reserve_turn(db, identity, owner, prompt="恢复测试", expected_turn=0, request_key="crash")
        assert not created and replay.id == turn_id
        next_turn, created = await store.reserve_turn(db, identity, owner, prompt="继续", expected_turn=1, request_key="continue")
        assert created
        await store.finish_generation(db, identity, next_turn.id, owner, status="interrupted")


@pytest.mark.asyncio
async def test_recent_heartbeat_and_unbounded_scan_are_rejected(rag_sessions, rag_users, monkeypatch) -> None:
    """任务创建很久但当前心跳新鲜时不能回收。"""
    identity, _ = await setup(rag_sessions, rag_users[0], monkeypatch)
    store = ConversationStore()
    async with rag_sessions() as db:
        turn, _ = await store.reserve_turn(db, identity, rag_users[0], prompt="仍活跃", expected_turn=0, request_key="live")
        turn_id = turn.id
        task = await db.get(EvaluationTask, turn.task_id)
        task.created_at = datetime.utcnow() - timedelta(days=2)
        await db.commit()
    await recover_generations(rag_sessions, limit=100)
    async with rag_sessions() as db:
        assert (await db.get(ConversationTurn, turn_id)).generation_status == "generating"
        await store.finish_generation(db, identity, turn_id, rag_users[0], status="interrupted")
    with pytest.raises(ValueError):
        await recover_generations(rag_sessions, limit=0)

@pytest.mark.asyncio
async def test_rag_recovery_preserves_stage_fees_and_rejects_late_storage(rag_sessions, rag_users, monkeypatch) -> None:
    """活跃长 RAG 可继续写入；心跳失活后原子结算，迟到持久化与重复恢复不再收费。"""
    import asyncio
    from app.models.rag import RagResponseDetail
    from app.models.token_usage import TokenUsageLog
    from app.schemas.rag import RagTaskContext
    from app.services.rag.clients import RagClientError
    from test_multiturn_rag_generation import setup_generator

    owner = rag_users[0]
    generator, identity, _, _ = await setup_generator(rag_sessions, owner, monkeypatch, wait=True)
    stream = generator.stream(identity, owner, TurnCreate(
        prompt="长RAG", expectedTurn=0, requestKey="rag-crash"))
    try:
        async for event in stream:
            if event["type"] == "delta":
                break
        async with rag_sessions() as db:
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == identity))
            task = await db.get(EvaluationTask, turn.task_id)
            task.created_at = datetime.utcnow() - timedelta(days=1)
            response = await db.scalar(select(ModelResponse).where(ModelResponse.task_id == task.id).order_by(ModelResponse.id))
            detail = await db.scalar(select(RagResponseDetail).where(RagResponseDetail.response_id == response.id))
            response_id, task_id, turn_id = response.id, task.id, turn.id
            context = RagTaskContext(task_id=task.id, user_id=owner, knowledge_base_id=detail.knowledge_base_id,
                content_revision=detail.content_revision, prompt=task.prompt,
                document_versions=[(item["documentId"], item["indexRevision"]) for item in detail.document_versions_json])
            await db.commit()
        # 同一写入权限校验允许心跳新鲜的长任务，不受旧单轮创建期限限制。
        assert await generator.rag_store.saved_answer(context, response_id) == ""
        heartbeats = [task for task in asyncio.all_tasks() if task.get_name().startswith(f"conversation-heartbeat:{identity}:")]
        for heartbeat in heartbeats:
            heartbeat.cancel()
        await asyncio.gather(*heartbeats, return_exceptions=True)
        async with rag_sessions() as db:
            conversation = await db.get(Conversation, identity)
            conversation.updated_at = datetime.utcnow() - timedelta(hours=5)
            await db.commit()
        await recover_generations(rag_sessions, limit=100)
        await recover_generations(rag_sessions, limit=100)
        with pytest.raises(RagClientError, match="任务已结束"):
            await generator.rag_store.saved_answer(context, response_id)
        async with rag_sessions() as db:
            assert (await db.get(ConversationTurn, turn_id)).generation_status == "interrupted"
            responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id == task_id))).all()
            assert all(response.status == "failed" for response in responses)
            detail = await db.scalar(select(RagResponseDetail).where(RagResponseDetail.response_id == response_id))
            assert any(stage["status"] == "unknown" for stage in detail.stage_usage_json)
            assert not any(stage["status"] == "pending" for stage in detail.stage_usage_json)
            logs = (await db.scalars(select(TokenUsageLog).where(TokenUsageLog.task_id == task_id))).all()
            assert len(logs) == len(responses) and sum(log.total_tokens for log in logs) > 0
    finally:
        await stream.aclose()
    async with rag_sessions() as db:
        assert (await db.get(EvaluationTask, task_id)).status == "interrupted"
