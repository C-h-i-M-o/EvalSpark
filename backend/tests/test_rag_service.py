import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.adapters.base import ModelReply, ModelUsage
from app.models.evaluation import EvaluationTask
from app.models.response import ModelResponse
from app.services.evaluation_service import EvaluationService
from app.services.rag.clients import RagClientError, VectorMatch
from app.services.rag.evaluation import RagEvaluationRunner
from app.services.rag.judge import run_rag_judge
from app.services.rag.service import RagEvaluationService, RagRun
from app.services.rag.usage import external_stage
from app.services.token_quota_service import token_quota_service
from test_rag_evaluation import Candidate, Embedding, model
from test_rag_evaluation_store import stored
from test_rag_judge import result_payload


class FixedVectors:
    async def search(self, *args, **kwargs):
        return [VectorMatch("chunk", 1, 2, 0, 0.8)]


@pytest.mark.asyncio
async def test_duplicate_model_config_cannot_judge_itself(monkeypatch) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock
    from app.schemas.evaluation import EvaluationTaskCreate
    from app.services.rag.errors import KnowledgeBaseError
    candidate = model(1)
    duplicate = replace(candidate, id=2, display_name="另一配置")
    resolve = AsyncMock(side_effect=[[candidate], [duplicate]])
    monkeypatch.setattr("app.services.rag.service.model_config_service.resolve_runtime_models", resolve)
    payload = EvaluationTaskCreate(taskType="rag", prompt="问题", modelIds=[1], judgeModelId=2, knowledgeBaseId=1)
    with pytest.raises(KnowledgeBaseError, match="不同"):
        await RagEvaluationService().start(payload, AsyncMock(), 1)


class FixedJudge:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, request):
        self.calls += 1
        return ModelReply(json.dumps(result_payload()), ModelUsage(10, 2), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_full_service_scores_before_final_response_and_history_matches(stored, monkeypatch, visibility) -> None:
    store, context, prepared, engine = stored
    with Session(engine) as db:
        db.get(EvaluationTask, context.task_id).visibility = visibility
        db.commit()
    candidate, judge = Candidate("申请材料"), FixedJudge()
    runner = RagEvaluationRunner(store, embedding=Embedding(), vectors=FixedVectors(), client_factory=lambda _: candidate)
    service = RagEvaluationService(store, runner)
    async def judge_with_fake_client(*args):
        return await run_rag_judge(*args, client_factory=lambda _: judge)
    monkeypatch.setattr("app.services.rag.service.run_rag_judge", judge_with_fake_client)
    recorded = []
    async def record(db, *, user_id, task_id, response_id, model_config_id, total_tokens):
        from app.models.token_usage import TokenUsageLog
        recorded.append(total_tokens)
        db.add(TokenUsageLog(user_id=user_id, task_id=task_id, response_id=response_id,
            model_config_id=model_config_id, total_tokens=total_tokens, usage_date=token_quota_service.usage_date()))
    monkeypatch.setattr(token_quota_service, "record_usage", record)
    run = RagRun(context, prepared, [model(1)], model(2))
    events = [event async for event in service.stream(run, EvaluationService())]
    types = [event["type"] for event in events]
    assert types[0] == "task_started" and types[-1] == "task_completed"
    assert types.index("rag_retrieval") < types.index("model_delta") < types.index("model_response")
    assert "rag_answer_ready" not in types and judge.calls == 3
    response = next(event["response"] for event in events if event["type"] == "model_response")
    task = events[-1]["task"]
    assert response == task.responses[0] and response.score.final is not None
    assert response.rag.external_total_tokens == 73 and recorded == [73]
    async with store.sessions() as db:
        history = await EvaluationService().get_task(context.task_id, db, 1)
        assert history.model_dump() == task.model_dump()


@pytest.mark.asyncio
async def test_closing_stream_waits_for_model_cancellation_and_persists_failure(stored, monkeypatch) -> None:
    store, context, prepared, engine = stored
    started, stopped = asyncio.Event(), asyncio.Event()
    class HangingCandidate(Candidate):
        async def chat(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
    runner = RagEvaluationRunner(store, embedding=Embedding(), vectors=FixedVectors(),
        client_factory=lambda _: HangingCandidate("查询"))
    async def record(*args, **kwargs):
        return True
    monkeypatch.setattr(token_quota_service, "record_rag_usage", record)
    service = RagEvaluationService(store, runner)
    stream = service.stream(RagRun(context, prepared, [model(1)], model(2)), EvaluationService())
    assert (await anext(stream))["type"] == "task_started"
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.wait_for(stream.aclose(), 1)
    assert stopped.is_set()
    with Session(engine) as db:
        assert db.get(EvaluationTask, context.task_id).status == "failed"


@pytest.mark.asyncio
async def test_recovery_only_closes_stale_tasks_and_late_write_is_rejected(stored, monkeypatch) -> None:
    store, context, prepared, engine = stored
    calls = []
    async def record(db, *, response_id, user_id):
        calls.append(response_id)
        return True
    monkeypatch.setattr(token_quota_service, "record_rag_usage", record)
    await store.recover_interrupted()
    assert calls == []
    with Session(engine) as db:
        db.get(EvaluationTask, context.task_id).created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=61)
        db.commit()
    await store.recover_interrupted()
    await store.recover_interrupted()
    assert calls == [prepared[0].response_id]
    with Session(engine) as db:
        assert db.get(ModelResponse, prepared[0].response_id).status == "failed"
    with pytest.raises(RagClientError, match="任务已结束"):
        await store.save_stage(context, prepared[0].response_id, external_stage("rewrite", model(1), None), start=True)
