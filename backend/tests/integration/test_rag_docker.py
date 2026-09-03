"""真实隔离存储/Worker/HTTP 模型链路；收费模型由 model-test 替代。

捕捉丢失评分写入、事件过早完成、跨用户内容泄漏、重复记账和删除丢快照。
ASGI 客户端不模拟浏览器断线；该项仍需真实代理与浏览器验收。
"""
import json
import os
from collections.abc import AsyncIterator
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.api_keys import store_api_key
from app.core.config import settings
from app.core.security import AUTH_COOKIE_NAME, create_access_token
from app.db.session import engine
from app.main import app
from app.models.model_config import ModelConfig, ModelProvider
from app.models.token_usage import TokenUsageLog
from app.models.user import User
from app.schemas.evaluation import EvaluationTaskRead
from test_rag_lifecycle import wait_job

pytestmark = pytest.mark.skipif(
    os.environ.get("RAG_ACCEPTANCE_TESTS") != "1", reason="需要显式启用完整隔离验收",
)


@pytest.fixture(autouse=True)
def isolated_endpoints() -> None:
    # 在连接或写入前拒绝误用业务服务；数据库名另由 rag_sessions 的上游 fixture 核对。
    assert os.environ.get("RAG_INTEGRATION_TESTS") == "1"
    database = make_url(settings.database_url)
    assert database.host == "mysql-test" and database.database == "multichateval_rag_test"
    assert settings.rag_embedding_url.host == "embedding-test"
    assert settings.rag_qdrant_url.host == "qdrant-test"
    assert settings.rag_redis_url.host == "redis-test"
    assert str(settings.rag_documents_dir) == "/test-documents"


@pytest_asyncio.fixture
async def api_clients(rag_users: tuple[int, int]) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    owner, admin = rag_users
    # 不覆盖鉴权依赖；用测试密钥签名的 Cookie 经真实 JWT、用户状态与归属校验。
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://api-test",
            cookies={AUTH_COOKIE_NAME: create_access_token(owner)}, timeout=180) as client,
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://api-test",
            cookies={AUTH_COOKIE_NAME: create_access_token(admin)}, timeout=180) as other,
    ):
        yield client, other
    # API 全局连接池不能跨 pytest 的函数事件循环复用连接。
    await engine.dispose()


@pytest_asyncio.fixture
async def model_ids(rag_sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with rag_sessions() as db:
        provider = ModelProvider(name=f"验收_{uuid4().hex}", base_url="http://model-test:8080/v1",
            api_key_encrypted=store_api_key("rag-acceptance-only"), enabled=True)
        db.add(provider)
        await db.flush()
        configs = [ModelConfig(provider_id=provider.id, model_name=name, display_name=f"验收 {name}",
            currency="USD" if name == "judge" else "CNY", price_input=Decimal("1"),
            price_output=Decimal("2"), timeout_seconds=30, max_tokens=2048, enabled=True)
            for name in ("candidate-a", "candidate-b", "candidate-fail", "judge")]
        db.add_all(configs)
        await db.commit()
        return {model.model_name: model.id for model in configs}


async def create_ready_library(client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    created = await client.post("/api/knowledge-bases", json={"name": f"合成验收_{uuid4().hex}"})
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]
    uploaded = await client.post(f"/api/knowledge-bases/{kb_id}/documents", files={
        "file": ("报销指南.txt", "报销申请需要发票和主管审批，申请人应保留电子凭证。".encode(), "text/plain"),
    })
    assert uploaded.status_code == 202, uploaded.text
    accepted = uploaded.json()
    await wait_job(sessions, accepted["jobId"], seconds=300)
    current = await client.get(f"/api/knowledge-bases/{kb_id}")
    assert current.status_code == 200 and current.json()["available"] is True
    return kb_id, accepted["documentId"]


@pytest.mark.asyncio
async def test_scored_stream_is_private_accounted_and_keeps_deleted_evidence(
    api_clients: tuple[httpx.AsyncClient, httpx.AsyncClient], model_ids: dict[str, int],
    rag_sessions: async_sessionmaker[AsyncSession], rag_users: tuple[int, int],
) -> None:
    client, admin = api_clients
    kb_id, document_id = await create_ready_library(client, rag_sessions)
    result = await client.post("/api/evaluation/tasks/stream", json={
        "taskType": "rag", "knowledgeBaseId": kb_id, "prompt": "报销需要哪些材料和审批？",
        "modelIds": [model_ids["candidate-a"], model_ids["candidate-b"]],
        "judgeModelId": model_ids["judge"], "enableJudge": True, "visibility": "public",
    })
    assert result.status_code == 200 and "application/x-ndjson" in result.headers["content-type"]
    events = [json.loads(line) for line in result.text.splitlines()]
    assert events[0]["type"] == "task_started" and events[-1]["type"] == "task_completed"
    task = EvaluationTaskRead.model_validate(events[-1]["task"])
    assert task.task_type == "rag" and task.visibility == "private" and task.status == "completed"
    assert len(task.responses) == 2
    assert all(response.rag is not None for response in task.responses)
    assert {response.rag.rewritten_query for response in task.responses} == {"报销发票申请材料", "主管审批电子凭证"}
    for response in task.responses:
        assert response.status == "success" and response.score.score_status == "scored"
        detail = response.rag
        assert detail is not None and detail.score_version == "rag-v1"
        assert 1 <= len(detail.evidence) <= 5
        assert all(item.document_id == document_id and item.source.kind == "text" for item in detail.evidence)
        assert detail.evidence[0].label == "S1" and "报销申请需要发票" in detail.evidence[0].text
        assert detail.judge_aggregate.valid_run_count == 3
        assert [run.run_index for run in detail.judge_runs] == [1, 2, 3]
        assert detail.faithfulness == 9 and detail.citation_correctness == 8 and detail.citation_completeness == 7
        assert detail.rag_final == Decimal("8.3")
        assert response.score.final == float((Decimal(str(response.score.rule_final)) * Decimal("0.2")
            + Decimal("6.55")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        # 2 次候选调用各 13 Token，3 次 Judge 各 24 Token；本地 Embedding 不重复扣额度。
        assert detail.external_total_tokens == 98 and detail.has_unknown_usage is False
        assert detail.cost_by_currency == {"CNY": Decimal("0.000032"), "USD": Decimal("0.000084")}
        assert len(detail.stage_usage) == 6 and all(item.status == "known" for item in detail.stage_usage)
        own = [(index, event) for index, event in enumerate(events) if event.get("modelConfigId") == response.model_config_id]
        retrieval = next(index for index, event in own if event["type"] == "rag_retrieval")
        delta = next(index for index, event in own if event["type"] == "model_delta")
        judging = next(index for index, event in own if event["type"] == "rag_stage" and event["stage"] == "judging")
        finalized = next(index for index, event in enumerate(events)
            if event["type"] == "model_response" and event["response"]["id"] == response.id)
        assert retrieval < delta < judging < finalized < len(events) - 1
        assert events[finalized]["response"] == response.model_dump(mode="json", by_alias=True)

    task_path = f"/api/evaluation/tasks/{task.task_id}"
    history = await client.get(task_path)
    assert history.status_code == 200 and history.json() == task.model_dump(mode="json", by_alias=True)
    own_list = (await client.get("/api/evaluation/tasks", params={"taskType": "rag"})).json()
    assert task.task_id in {item["taskId"] for item in own_list["items"]}
    assert (await client.get("/api/evaluation/tasks", params={"taskType": "chat"})).json()["total"] == 0
    response_id = task.responses[0].id
    response_path = f"/api/evaluation/responses/{response_id}"
    comment = await client.post(f"{response_path}/comments", json={"content": "仅本人的合成验收评论"})
    assert comment.status_code == 201, comment.text
    comment_id = comment.json()["id"]

    # 管理员和其他普通用户均没有私有资料读取豁免。
    async with rag_sessions() as db:
        stranger = User(username=f"rag_stranger_{uuid4().hex}", password_hash="test-only", role="user", status="active")
        db.add(stranger)
        await db.commit()
        stranger_id = stranger.id
    for outsider in (rag_users[1], stranger_id):
        admin.cookies.set(AUTH_COOKIE_NAME, create_access_token(outsider))
        for path in (task_path, f"{response_path}/comments", f"/api/knowledge-bases/{kb_id}",
                     f"/api/knowledge-bases/{kb_id}/documents/{document_id}/download"):
            assert (await admin.get(path)).status_code == 404
        assert (await admin.post(f"{response_path}/feedback", json={"feedbackType": "like"})).status_code == 404
        assert (await admin.post(f"{response_path}/comments", json={"content": "不应写入"})).status_code == 404
        assert (await admin.delete(f"/api/evaluation/comments/{comment_id}")).status_code == 404
        listing = (await admin.get("/api/evaluation/tasks", params={"taskType": "rag"})).json()
        assert task.task_id not in {item["taskId"] for item in listing["items"]}
    admin.cookies.set(AUTH_COOKIE_NAME, create_access_token(rag_users[1]))
    stats = await admin.get("/api/admin/feedback-stats")
    assert stats.status_code == 200
    assert task.prompt not in stats.text and "仅本人的合成验收评论" not in stats.text

    liked = await client.post(f"{response_path}/feedback", json={"feedbackType": "like"})
    assert liked.status_code == 200 and liked.json()["active"] is True
    base = task.responses[0].rag.base_final
    assert liked.json()["score"]["final"] == float((base * Decimal("0.9") + 1).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    removed = await client.post(f"{response_path}/feedback", json={"feedbackType": "like"})
    assert removed.status_code == 200 and removed.json()["active"] is False
    assert removed.json()["score"]["final"] == task.responses[0].score.final
    async with rag_sessions() as db:
        count, tokens = (await db.execute(select(func.count(), func.sum(TokenUsageLog.total_tokens))
            .where(TokenUsageLog.task_id == task.task_id))).one()
        assert count == 2 and tokens == 196

    deleted = await client.delete(f"/api/knowledge-bases/{kb_id}")
    assert deleted.status_code == 202
    assert (await client.get(f"/api/knowledge-bases/{kb_id}/documents/{document_id}/download")).status_code == 404
    await wait_job(rag_sessions, deleted.json()["jobId"], seconds=300)
    snapshot = EvaluationTaskRead.model_validate((await client.get(task_path)).json())
    for before, after in zip(task.responses, snapshot.responses, strict=True):
        assert before.rag == after.rag and before.answer == after.answer and before.score.final == after.score.final
    assert (await admin.get(task_path)).status_code == 404


@pytest.mark.asyncio
async def test_failed_candidate_does_not_erase_other_answer_or_invent_usage(
    api_clients: tuple[httpx.AsyncClient, httpx.AsyncClient], model_ids: dict[str, int],
    rag_sessions: async_sessionmaker[AsyncSession],
) -> None:
    client, _ = api_clients
    kb_id, _ = await create_ready_library(client, rag_sessions)
    response = await client.post("/api/evaluation/tasks", json={
        "taskType": "rag", "knowledgeBaseId": kb_id, "prompt": "报销需要什么？",
        "modelIds": [model_ids["candidate-a"], model_ids["candidate-fail"]],
        "judgeModelId": model_ids["judge"], "enableJudge": True,
    })
    assert response.status_code == 200, response.text
    task = EvaluationTaskRead.model_validate(response.json())
    good = next(item for item in task.responses if item.model_config_id == model_ids["candidate-a"])
    failed = next(item for item in task.responses if item.model_config_id == model_ids["candidate-fail"])
    assert good.score.score_status == "scored" and good.rag.external_total_tokens == 98
    assert failed.score.score_status == "model_failed" and failed.score.final is None and failed.score.excluded_from_stats
    assert failed.rag.failure_stage == "rewrite" and failed.rag.has_unknown_usage
    assert failed.rag.external_total_tokens == 0 and failed.rag.judge_runs == []
    assert len(failed.rag.stage_usage) == 1 and failed.rag.stage_usage[0].status == "unknown"
    # 成功路径清理本次新建库；失败时保留测试资料供排查，不清空数据库。
    deleted = await client.delete(f"/api/knowledge-bases/{kb_id}")
    assert deleted.status_code == 202
    await wait_job(rag_sessions, deleted.json()["jobId"], seconds=300)
