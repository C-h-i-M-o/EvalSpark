"""验证会话接口参数和身份传递，实际权限另由隔离数据库测试覆盖。"""

from datetime import datetime
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.api.dependencies import get_current_user
from app.main import app
from app.models.user import User
from app.schemas.conversation import ConversationRead
from app.services.multiturn.catalog import conversation_catalog
from app.services.multiturn.store import ConversationError
from app.services.token_quota_service import TokenQuotaExceededError
from app.api.v1.conversations import conversation_generator
import app.api.v1.conversations as conversation_routes
from app.services.multiturn.assessment_reader import assessment_reader
from app.schemas.conversation import AssessmentDetailRead, AssessmentListRead


@pytest.fixture
def client():
    """仅覆写当前用户，服务方法由各测试独立替换。"""
    app.dependency_overrides[get_current_user] = lambda: User(id=7, username="多轮测试", password_hash="unused", role="user", status="active")
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


@pytest.fixture
def stream_scope(monkeypatch):
    """流协议单元测试固定已授权普通会话，实际鉴权由隔离 HTTP 测试覆盖。"""
    scope = AsyncMock(return_value=SimpleNamespace(mode="chat"))
    monkeypatch.setattr(conversation_routes.conversation_store, "get", scope)
    return scope


def test_create_passes_authenticated_owner_and_rejects_history(client, monkeypatch) -> None:
    """创建会话不能伪造所有者，也不能将客户端历史带入服务。"""
    response = ConversationRead(id=1, title="会话", mode="chat", ownerId=7, canContinue=True,
        visibility="private", currentTurn=0, generationStatus="idle", configuration={},
        createdAt=datetime(2026, 9, 18), updatedAt=None)
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(conversation_catalog, "create", create)
    result = client.post("/api/evaluation/conversations", json={"modelIds": [1]})
    assert result.status_code == 201 and result.json()["ownerId"] == 7
    assert create.await_args.args[1] == 7
    rejected = client.post("/api/evaluation/conversations", json={"modelIds": [1], "ownerId": 9, "history": []})
    assert rejected.status_code == 422 and create.await_count == 1


def test_private_access_uses_uniform_not_found(client, monkeypatch) -> None:
    """越权响应只包含中文错误和稳定错误码。"""
    monkeypatch.setattr(conversation_catalog, "get", AsyncMock(side_effect=ConversationError("conversation_not_found", "会话不存在或无权访问", 404)))
    result = client.get("/api/evaluation/conversations/12")
    assert result.status_code == 404 and result.json()["detail"]["code"] == "conversation_not_found"


def test_pagination_is_bounded(client) -> None:
    """错误分页在访问数据库前被拒绝。"""
    assert client.get("/api/evaluation/conversations?taskType=chat&pageSize=101").status_code == 422
    assert client.get("/api/evaluation/conversations/1/turns?page=0").status_code == 422
    assert client.get("/api/evaluation/conversations/1/assessments?pageSize=101").status_code == 422
    assert client.get("/api/evaluation/conversations/1/assessments?responseId=0").status_code == 422


def test_formal_assessment_uses_server_source_and_authenticated_owner(client, monkeypatch) -> None:
    """正式提交只接受暂定评分 ID，并拒绝客户端自带检查项或分数。"""
    result = AssessmentDetailRead(id=9, responseId=42, modelConfigId=2, throughTurn=1,
        scoreVersion="chat-multiturn-v1", status="queued", formal=True, result=None,
        createdAt=datetime(2026, 9, 18), completedAt=None, runs=[])
    submit = AsyncMock(return_value=result)
    monkeypatch.setattr(conversation_routes, "submit_reassessment", submit)
    response = client.post("/api/evaluation/conversations/8/assessments", json={"sourceAssessmentId": 5})
    assert response.status_code == 202 and response.json()["formal"] is True
    assert submit.await_args.args[1:] == (8, 7, 5)
    for payload in ({"sourceAssessmentId": True}, {"sourceAssessmentId": 0},
                    {"sourceAssessmentId": 5, "checks": []}, {"sourceAssessmentId": 5, "score": 10}):
        assert client.post("/api/evaluation/conversations/8/assessments", json=payload).status_code == 422
    assert submit.await_count == 1


def test_assessment_routes_forward_identity_and_filters(client, monkeypatch) -> None:
    """评分读取传递登录身份和筛选条件，空结果不伪造评分。"""
    listing = AsyncMock(return_value=AssessmentListRead(items=[], total=0, page=2, pageSize=3))
    monkeypatch.setattr(assessment_reader, "list", listing)
    response = client.get("/api/evaluation/conversations/8/assessments?page=2&pageSize=3&responseId=42")
    assert response.status_code == 200 and response.json()["items"] == []
    assert listing.await_args.args[1:] == (8, 7)
    assert listing.await_args.kwargs == {"page": 2, "page_size": 3, "response_id": 42}
    detail = AsyncMock(return_value=AssessmentDetailRead(id=5, responseId=42, modelConfigId=2,
        throughTurn=1, scoreVersion="chat-multiturn-v1", status="queued", formal=False,
        result=None, createdAt=datetime(2026, 9, 18), completedAt=None, runs=[]))
    monkeypatch.setattr(assessment_reader, "get", detail)
    response = client.get("/api/evaluation/conversations/8/assessments/5")
    assert response.status_code == 200 and response.json()["result"] is None
    assert detail.await_args.args[1:] == (8, 5, 7)


def test_stream_uses_authenticated_owner_and_closes_generator(client, monkeypatch, stream_scope) -> None:
    """NDJSON 返回真实事件顺序，作者来自登录态，响应结束执行收尾。"""
    calls = []
    closed = []

    async def stream(conversation_id, owner, payload):
        """模拟流式运行服务，不访问数据库或供应商。"""
        calls.append((conversation_id, owner, payload.prompt))
        try:
            yield {"type": "turn_started", "turnId": 3, "replayed": False}
            yield {"type": "delta", "modelConfigId": 2, "delta": "你好"}
            yield {"type": "turn_completed", "turnId": 3}
        finally:
            closed.append(True)

    monkeypatch.setattr(conversation_generator, "stream", stream)
    response = client.post("/api/evaluation/conversations/8/turns/stream",
                           json={"prompt": "继续", "expectedTurn": 2, "requestKey": "next"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert [json.loads(line)["type"] for line in response.text.splitlines()] == ["turn_started", "delta", "turn_completed"]
    assert calls == [(8, 7, "继续")] and closed == [True]
    assert stream_scope.await_args.args[1:] == (8, 7)
    assert stream_scope.await_args.kwargs == {"owner_only": True}


@pytest.mark.parametrize("error,status", [
    (ConversationError("conversation_not_found", "会话不存在或无权访问", 404), 404),
    (ConversationError("conversation_turn_conflict", "轮次已变化", 409), 409),
    (TokenQuotaExceededError("限额"), 429),
])
def test_stream_preflight_returns_http_error_before_headers(client, monkeypatch, stream_scope, error, status) -> None:
    """权限、轮次和额度错误不能被包装为成功流。"""
    async def fail(*args):
        """首次事件前拒绝请求。"""
        raise error
        yield {}  # 保持异步生成器协议，异常路径不会执行。

    monkeypatch.setattr(conversation_generator, "stream", fail)
    response = client.post("/api/evaluation/conversations/8/turns/stream",
                           json={"prompt": "继续", "expectedTurn": 2, "requestKey": "next"})
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")


def test_stream_runtime_error_is_sanitized(client, monkeypatch, stream_scope) -> None:
    """响应开始后仅发送稳定错误码，不暴露供应商错误正文。"""
    async def fail(*args):
        """模拟生成期间内部异常。"""
        yield {"type": "turn_started"}
        raise RuntimeError("sensitive-provider-key")

    monkeypatch.setattr(conversation_generator, "stream", fail)
    response = client.post("/api/evaluation/conversations/8/turns/stream",
                           json={"prompt": "继续", "expectedTurn": 2, "requestKey": "next"})
    assert response.status_code == 200
    assert json.loads(response.text.splitlines()[-1])["type"] == "stream_error"
    assert "sensitive" not in response.text


def test_stream_rejects_oversized_multibyte_prompt_before_generation(client, monkeypatch) -> None:
    """限制 UTF-8 字节而非字符，避免超过现有 TEXT 容量。"""
    response = client.post("/api/evaluation/conversations/8/turns/stream",
                           json={"prompt": "中" * 22000, "expectedTurn": 0, "requestKey": "next"})
    assert response.status_code == 422


def test_branch_retry_uses_server_turn_prompt_after_rollback(client, monkeypatch) -> None:
    """分支重试在回滚分发事务后仍使用服务端轮次原问题，拒绝客户端改写字段。"""
    db = SimpleNamespace(
        rollback=AsyncMock(),
        scalar=AsyncMock(return_value=SimpleNamespace(id=31, turn_index=3, prompt="服务端原问题")),
    )
    monkeypatch.setitem(app.dependency_overrides, conversation_routes.get_db, lambda: db)
    monkeypatch.setattr(conversation_routes.conversation_store, "get", AsyncMock(return_value=SimpleNamespace(mode="chat")))
    seen = []

    async def stream(conversation_id, owner, question, branch_action=None):
        """记录服务端重建的续聊请求。"""
        seen.append((conversation_id, owner, question.prompt, question.expected_turn, branch_action.action))
        yield {"type": "turn_started", "turnId": 31, "turn": 3, "replayed": False}

    monkeypatch.setattr(conversation_generator, "stream", stream)
    response = client.post("/api/evaluation/conversations/8/branches/stream", json={
        "turnId": 31, "modelConfigId": 2, "requestKey": "retry-1", "action": "retry", "prompt": "客户端篡改"
    })
    assert response.status_code == 422
    response = client.post("/api/evaluation/conversations/8/branches/stream", json={
        "turnId": 31, "modelConfigId": 2, "requestKey": "retry-1", "action": "retry"
    })
    assert response.status_code == 200
    assert seen == [(8, 7, "服务端原问题", 2, "retry")]
    db.rollback.assert_awaited_once()


def test_branch_skip_does_not_call_generation(client, monkeypatch) -> None:
    """跳过分支只保存占位回答，不调用普通生成器。"""
    db = SimpleNamespace(
        rollback=AsyncMock(),
        scalar=AsyncMock(return_value=SimpleNamespace(id=31, turn_index=3, prompt="服务端原问题")),
    )
    monkeypatch.setitem(app.dependency_overrides, conversation_routes.get_db, lambda: db)
    monkeypatch.setattr(conversation_routes.conversation_store, "get", AsyncMock(return_value=SimpleNamespace(mode="chat")))
    reserve = AsyncMock(return_value=(SimpleNamespace(id=31, task_id=88, turn_index=3),
                                      SimpleNamespace(id=99), 2, True))
    monkeypatch.setattr(conversation_routes, "reserve_branch_action", reserve)
    generation = AsyncMock(side_effect=AssertionError("skip 不应调用普通生成器"))
    rag_generation = AsyncMock(side_effect=AssertionError("skip 不应调用 RAG 生成器"))
    monkeypatch.setattr(conversation_generator, "stream", generation)
    monkeypatch.setattr(conversation_routes.rag_conversation_generator, "stream", rag_generation)
    response = client.post("/api/evaluation/conversations/8/branches/stream", json={
        "turnId": 31, "modelConfigId": 2, "requestKey": "skip-1", "action": "skip"
    })
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "turn_started" and events[1]["errorCode"] == "branch_skipped"
    generation.assert_not_called()
    rag_generation.assert_not_called()


@pytest.mark.parametrize("action", ["turn", "retry", "skip"])
def test_stream_routes_keep_owner_scalar_after_rollback(client, monkeypatch, action: str) -> None:
    """普通流和分支流回滚后不能再次读取已过期的登录 ORM 对象。"""
    class LoginIdentity:
        """回滚后模拟 ORM 实例失效的轻量登录对象。"""

        def __init__(self) -> None:
            """默认登录实例尚未过期。"""
            self.expired = False

        @property
        def id(self) -> int:
            """过期后读取标识立即失败，模拟回滚后的隐式数据库读取。"""
            if self.expired:
                raise AssertionError("回滚后不应读取 current_user.id")
            return 7

    actor = LoginIdentity()
    app.dependency_overrides[get_current_user] = lambda: actor
    turn = SimpleNamespace(id=31, turn_index=3, prompt="服务端原问题", task_id=88)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=turn),
        rollback=AsyncMock(side_effect=lambda: setattr(actor, "expired", True)),
    )
    monkeypatch.setitem(app.dependency_overrides, conversation_routes.get_db, lambda: db)
    monkeypatch.setattr(conversation_routes.conversation_store, "get", AsyncMock(return_value=SimpleNamespace(mode="chat")))
    normal = AsyncMock(side_effect=AssertionError("不应调用错误分支生成器"))
    rag = AsyncMock(side_effect=AssertionError("不应调用错误 RAG 生成器"))

    async def events(*args, **kwargs):
        """模拟普通流或 retry 流的首事件。"""
        yield {"type": "turn_started", "turnId": 31, "turn": 3, "replayed": False}

    monkeypatch.setattr(conversation_generator, "stream", events)
    monkeypatch.setattr(conversation_routes.rag_conversation_generator, "stream", rag)
    if action == "turn":
        path = "/api/evaluation/conversations/8/turns/stream"
        payload = {"prompt": "问题", "expectedTurn": 2, "requestKey": "owner-turn"}
    else:
        path = "/api/evaluation/conversations/8/branches/stream"
        payload = {"turnId": 31, "modelConfigId": 2, "requestKey": f"owner-{action}", "action": action}
        if action == "skip":
            monkeypatch.setattr(conversation_routes, "reserve_branch_action", AsyncMock(return_value=(turn, SimpleNamespace(id=99), 2, True)))
            monkeypatch.setattr(conversation_generator, "stream", normal)
    response = client.post(path, json=payload)
    assert response.status_code == 200
    normal.assert_not_called()
    rag.assert_not_called()
