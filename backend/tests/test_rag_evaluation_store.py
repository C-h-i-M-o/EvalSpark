import importlib
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models
from app.adapters.base import ModelReply, ModelUsage
from app.db.base import Base
from app.models.evaluation import EvaluationTask
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.model_config import ModelConfig, ModelProvider
from app.models.rag import RagResponseDetail
from app.models.response import ModelResponse
from app.models.user import User
from app.services.rag.clients import RagClientError, VectorMatch
from test_rag_evaluation import model


class Transaction:
    def __init__(self, transaction) -> None:
        self.transaction = transaction

    async def __aenter__(self):
        self.transaction.__enter__()

    async def __aexit__(self, *args):
        return self.transaction.__exit__(*args)


class AsyncTestSession:
    """SQLite 执行真实持久化 SQL；不把此替身当作 MySQL 行锁或并发验收。"""
    def __init__(self, engine) -> None:
        self.session = Session(engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        self.session.close()

    def begin(self):
        return Transaction(self.session.begin())

    def add(self, value) -> None:
        self.session.add(value)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def flush(self) -> None:
        self.session.flush()

    async def rollback(self) -> None:
        self.session.rollback()

    async def commit(self) -> None:
        self.session.commit()

    async def delete(self, value) -> None:
        self.session.delete(value)

    async def refresh(self, value) -> None:
        self.session.refresh(value)


@pytest_asyncio.fixture
async def stored():
    assert importlib.util.find_spec("app.services.rag.evaluation_store") is not None, "尚未实现 RAG 快照持久化"
    module = importlib.import_module("app.services.rag.evaluation_store")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([User(id=1, username="owner", password_hash="test"), User(id=2, username="other", password_hash="test")])
        db.add(ModelProvider(id=1, name="测试"))
        db.add(ModelConfig(id=1, provider_id=1, model_name="test", display_name="测试"))
        db.add(KnowledgeBase(id=1, user_id=1, name="资料", status="ready", content_revision=4))
        db.add(KnowledgeDocument(id=1, user_id=1, knowledge_base_id=1, original_name="原文.txt", storage_key="key",
            media_type="text/plain", size_bytes=20, content_hash="hash", status="ready", index_revision=2, chunk_count=1))
        db.add(KnowledgeChunk(id="chunk", document_id=1, user_id=1, knowledge_base_id=1, index_revision=2,
            chunk_index=0, text="不得丢失的证据", token_count=10, source_json={"kind": "text", "lineStart": 1, "lineEnd": 1}))
        db.commit()
    store = module.RagEvaluationStore(lambda: AsyncTestSession(engine))
    context, prepared = await store.create(1, 1, " 原始问题 ", [model(1)], enable_thinking=False)
    yield store, context, prepared, engine
    engine.dispose()


@pytest.mark.asyncio
async def test_create_persists_private_task_and_answer_before_external_calls(stored) -> None:
    _, context, prepared, engine = stored
    with Session(engine) as db:
        task = db.get(EvaluationTask, context.task_id)
        response = db.get(ModelResponse, prepared[0].response_id)
        detail = db.get(RagResponseDetail, response.id)
        assert task.prompt == " 原始问题 " and task.task_type == "rag" and task.visibility == "private"
        assert response.status == "pending" and detail.evidence_json == []
        assert detail.knowledge_base_name == "资料" and context.document_versions == [(1, 2)]
        assert "baseUrl" not in response.config_snapshot


@pytest.mark.asyncio
async def test_evidence_is_independent_of_deleted_current_text(stored) -> None:
    store, context, prepared, engine = stored
    item = prepared[0]
    item.rewritten_query = "独立查询"
    item.evidence = await store.read_evidence(context, [VectorMatch("chunk", 1, 2, 0, 0.7)])
    assert await store.fix_snapshots(context, prepared)
    with Session(engine) as db:
        db.get(KnowledgeBase, 1).status = "deleting"
        db.get(KnowledgeBase, 1).content_revision += 1
        db.delete(db.get(KnowledgeChunk, "chunk"))
        db.commit()
    await store.save_answer(context, item, "回答 [S1]", None)
    with Session(engine) as db:
        detail = db.get(RagResponseDetail, item.response_id)
        assert detail.evidence_json[0]["text"] == "不得丢失的证据"
        assert db.get(ModelResponse, item.response_id).answer_text == "回答 [S1]"


@pytest.mark.asyncio
async def test_changed_library_rejects_and_persists_failed_snapshot(stored) -> None:
    store, context, prepared, engine = stored
    prepared[0].evidence = await store.read_evidence(context, [VectorMatch("chunk", 1, 2, 0, 0.7)])
    with Session(engine) as db:
        db.get(KnowledgeBase, 1).content_revision += 1
        db.commit()
    assert not await store.fix_snapshots(context, prepared)
    with Session(engine) as db:
        detail = db.get(RagResponseDetail, prepared[0].response_id)
        assert detail.evidence_json == [] and detail.failure_stage == "snapshot"
        assert detail.error_code == "knowledge_base_changed"


@pytest.mark.asyncio
async def test_sql_evidence_read_rejects_wrong_owner_version_and_chunk_index(stored) -> None:
    store, context, _, _ = stored
    for scope, match in [
        (context.model_copy(update={"user_id": 2}), VectorMatch("chunk", 1, 2, 0, 0.7)),
        (context, VectorMatch("chunk", 1, 3, 0, 0.7)),
        (context, VectorMatch("chunk", 1, 2, 1, 0.7)),
    ]:
        with pytest.raises(RagClientError):
            await store.read_evidence(scope, [match])


@pytest.mark.asyncio
async def test_stage_start_is_durable_and_cannot_be_replayed(stored) -> None:
    from app.services.rag.usage import external_stage
    store, context, prepared, engine = stored
    response_id = prepared[0].response_id
    pending = external_stage("rewrite", model(1), None, pending=True)
    await store.save_stage(context, response_id, pending, start=True)
    with pytest.raises(RagClientError, match="重复"):
        await store.save_stage(context, response_id, pending, start=True)
    known = external_stage("rewrite", model(1), ModelReply("查询", ModelUsage(10, 2), 30))
    await store.save_stage(context, response_id, known)
    with Session(engine) as db:
        detail = db.get(RagResponseDetail, response_id)
        assert len(detail.stage_usage_json) == 1 and detail.stage_usage_json[0]["totalTokens"] == 12


@pytest.mark.asyncio
async def test_foreign_response_cannot_be_written_using_another_task_context(stored) -> None:
    from app.services.rag.usage import external_stage
    store, context, prepared, _ = stored
    forged = context.model_copy(update={"user_id": 2})
    with pytest.raises(RagClientError):
        await store.save_stage(forged, prepared[0].response_id, external_stage("rewrite", model(1), None), start=True)


@pytest.mark.asyncio
async def test_rag_usage_is_recorded_once_and_rolls_back_with_terminal_transaction(stored, monkeypatch) -> None:
    from app.models.token_usage import TokenUsageLog
    from app.services.token_quota_service import token_quota_service
    from app.services.rag.usage import external_stage
    assert hasattr(token_quota_service, "record_rag_usage"), "尚未实现 RAG 幂等汇总入账"
    store, context, prepared, engine = stored
    response_id = prepared[0].response_id
    usage = external_stage("rewrite", model(1), ModelReply("查询", ModelUsage(10, 2), 30))
    await store.save_stage(context, response_id, usage, start=True)
    with Session(engine) as db:
        db.get(ModelResponse, response_id).status = "failed"
        db.commit()
    calls: list[int] = []
    async def record(db, *, user_id: int, task_id: int, response_id: int,
                     model_config_id: int | None, total_tokens: int) -> None:
        # SQLite 验证上层幂等/事务；真实 MySQL 累加另有显式开关测试。
        calls.append(total_tokens)
        db.add(TokenUsageLog(user_id=user_id, task_id=task_id, response_id=response_id,
            model_config_id=model_config_id, total_tokens=total_tokens, usage_date=token_quota_service.usage_date()))
    monkeypatch.setattr(token_quota_service, "record_usage", record)
    with pytest.raises(RuntimeError):
        async with store.sessions() as db, db.begin():
            assert await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=context.user_id)
            raise RuntimeError("模拟终态事务提交前失败")
    async with store.sessions() as db, db.begin():
        assert await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=context.user_id)
    async with store.sessions() as db, db.begin():
        assert not await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=context.user_id)
    with Session(engine) as db:
        logs = db.scalars(select(TokenUsageLog).where(TokenUsageLog.response_id == response_id)).all()
        assert len(logs) == 1 and logs[0].total_tokens == 12
    assert calls == [12, 12]


@pytest.mark.asyncio
async def test_interrupted_task_keeps_known_usage_and_marks_unreturned_usage_unknown(stored, monkeypatch) -> None:
    from app.models.evaluation import EvaluationResult
    from app.services.rag.usage import external_stage
    from app.services.token_quota_service import token_quota_service
    store, context, prepared, engine = stored
    assert hasattr(store, "interrupt"), "尚未实现中断任务的非重放收尾"
    item = prepared[0]
    await store.save_stage(context, item.response_id, external_stage("rewrite", model(1), None, pending=True), start=True)
    recorded: list[int] = []
    async def record(db, *, response_id: int, user_id: int) -> bool:
        recorded.append(response_id)
        return True
    monkeypatch.setattr(token_quota_service, "record_rag_usage", record)
    await store.interrupt(context)
    await store.interrupt(context)
    with Session(engine) as db:
        detail = db.get(RagResponseDetail, item.response_id)
        result = db.scalar(select(EvaluationResult).where(EvaluationResult.response_id == item.response_id))
        assert detail.stage_usage_json[0]["status"] == "unknown"
        assert detail.stage_usage_json[0]["totalTokens"] is None
        assert detail.failure_stage == "rewrite" and detail.error_code == "rag_interrupted"
        assert result.final_score is None and result.excluded_from_stats
        assert db.get(EvaluationTask, context.task_id).status == "failed"
    assert recorded == [item.response_id]
