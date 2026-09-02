import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelUsage
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument
from app.models.model_config import ModelConfig, ModelProvider
from app.models.response import ModelResponse
from app.models.token_usage import DailyUserTokenUsage, TokenUsageLog
from app.services.rag.evaluation_store import RagEvaluationStore
from app.services.rag.clients import RagClientError
from app.services.rag.usage import external_stage
from app.services.token_quota_service import token_quota_service
from test_rag_evaluation import model


@pytest.mark.asyncio
async def test_real_mysql_concurrent_finalization_records_known_usage_once(rag_sessions, rag_users) -> None:
    """仅显式隔离库开关运行；不以 SQLite 结果替代 MySQL 行锁与累加验证。"""
    async with rag_sessions() as db:
        provider = ModelProvider(name=f"rag_usage_{uuid4().hex}")
        kb = KnowledgeBase(user_id=rag_users[0], name="用量测试", status="ready")
        db.add_all([provider, kb])
        await db.flush()
        config = ModelConfig(provider_id=provider.id, model_name="fake", display_name="测试模型")
        db.add(config)
        db.add(KnowledgeDocument(knowledge_base_id=kb.id, user_id=rag_users[0], original_name="fixture.txt",
            storage_key=uuid4().hex, media_type="text/plain", size_bytes=1, content_hash="test", status="ready", chunk_count=1))
        await db.commit()
        runtime = replace(model(1), id=config.id)
    store = RagEvaluationStore(rag_sessions)
    context, prepared = await store.create(rag_users[0], kb.id, "仅测试计费", [runtime], enable_thinking=False)
    response_id = prepared[0].response_id
    async def start() -> None:
        await store.save_stage(context, response_id, external_stage("rewrite", runtime, None, pending=True), start=True)
    starts = await asyncio.gather(start(), start(), return_exceptions=True)
    assert sum(value is None for value in starts) == 1
    assert sum(isinstance(value, RagClientError) for value in starts) == 1
    async with rag_sessions() as old_reader:
        # 先建立 REPEATABLE READ 旧快照；锁后读取明细必须是 current read。
        await old_reader.scalar(select(KnowledgeBase.id).where(KnowledgeBase.id == kb.id))
        await store.save_stage(context, response_id, external_stage("rewrite", runtime, ModelReply("查询", ModelUsage(10, 2), 1)))
        _, latest = await store._owned_response(old_reader, context, response_id)
        assert latest.stage_usage_json[0]["status"] == "known"
    async with rag_sessions() as db:
        (await db.get(ModelResponse, response_id)).status = "failed"
        await db.commit()
    async def finish() -> bool:
        async with rag_sessions() as db, db.begin():
            return await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=rag_users[0])
    async with rag_sessions() as old_reader:
        await old_reader.scalar(select(TokenUsageLog.id).where(TokenUsageLog.response_id == response_id))
        assert sorted(await asyncio.gather(finish(), finish())) == [False, True]
        assert not await token_quota_service.record_rag_usage(old_reader, response_id=response_id, user_id=rag_users[0])
    async with rag_sessions() as db:
        logs = (await db.scalars(select(TokenUsageLog).where(TokenUsageLog.response_id == response_id))).all()
        total = await db.scalar(select(DailyUserTokenUsage.total_tokens).where(DailyUserTokenUsage.user_id == rag_users[0]))
        assert len(logs) == 1 and logs[0].total_tokens == total == 12
