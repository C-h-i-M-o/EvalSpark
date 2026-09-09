from datetime import datetime

from fastapi.testclient import TestClient
import pytest

from app.api.dependencies import get_current_user
from app.main import app
from app.models.user import User
from app.schemas.evaluation import (
    CommentListRead,
    CommentRead,
    EvaluationTaskListItemRead,
    EvaluationTaskListRead,
    EvaluationTaskRead,
)
from app.services.evaluation_service import (
    EvaluationCommentNotFoundError,
    EvaluationResponseNotFoundError,
    evaluation_service,
)
from app.services.evaluation_service import EvaluationTaskNotFoundError, EvaluationTaskValidationError
from app.services.token_quota_service import token_quota_service


@pytest.fixture(autouse=True)
def authenticated_user() -> object:
    app.dependency_overrides[get_current_user] = lambda: User(
        id=7,
        username="test_user",
        password_hash="unused",
        role="user",
        status="active",
    )
    yield
    app.dependency_overrides.clear()


def test_create_evaluation_task_allows_missing_judge_model_when_judge_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ensure_can_start(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_create_task(payload: object, *_args: object, **_kwargs: object) -> EvaluationTaskRead:
        assert payload.enable_judge is True
        assert payload.judge_model_id is None
        return EvaluationTaskRead(
            taskId=12,
            status="completed",
            prompt=payload.prompt,
            responses=[],
        )

    monkeypatch.setattr(token_quota_service, "ensure_can_start", fake_ensure_can_start)
    monkeypatch.setattr(evaluation_service, "create_task", fake_create_task)

    response = TestClient(app).post(
        "/api/evaluation/tasks",
        json={
            "prompt": "测试问题",
            "modelIds": [1],
            "enableJudge": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["taskId"] == 12


def test_create_evaluation_task_returns_400_when_judge_model_is_tested(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ensure_can_start(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_create_task(*_args: object, **_kwargs: object) -> None:
        raise EvaluationTaskValidationError("LLM 评审模型不能同时作为被测模型")

    monkeypatch.setattr(token_quota_service, "ensure_can_start", fake_ensure_can_start)
    monkeypatch.setattr(evaluation_service, "create_task", fake_create_task)

    response = TestClient(app).post(
        "/api/evaluation/tasks",
        json={
            "prompt": "测试问题",
            "modelIds": [1],
            "enableJudge": True,
            "judgeModelId": 2,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "LLM 评审模型不能同时作为被测模型"}


def test_stream_evaluation_task_returns_400_when_judge_model_is_tested(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ensure_can_start(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_validate_task_models(*_args: object, **_kwargs: object) -> None:
        raise EvaluationTaskValidationError("LLM 评审模型不能同时作为被测模型")

    monkeypatch.setattr(token_quota_service, "ensure_can_start", fake_ensure_can_start)
    monkeypatch.setattr(evaluation_service, "validate_task_models", fake_validate_task_models)

    response = TestClient(app).post(
        "/api/evaluation/tasks/stream",
        json={
            "prompt": "测试问题",
            "modelIds": [1],
            "enableJudge": True,
            "judgeModelId": 2,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "LLM 评审模型不能同时作为被测模型"}


def test_get_evaluation_task_returns_404_when_task_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_task(task_id: int, _db: object, user_id: int) -> None:
        assert task_id == 404
        assert user_id == 7
        raise EvaluationTaskNotFoundError("评测任务不存在")

    monkeypatch.setattr(evaluation_service, "get_task", fake_get_task)

    response = TestClient(app).get("/api/evaluation/tasks/404")

    assert response.status_code == 404
    assert response.json() == {"detail": "评测任务不存在"}


def test_get_evaluation_task_returns_task_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime(2026, 6, 3, 12, 0, 0)

    async def fake_get_task(task_id: int, _db: object, user_id: int) -> EvaluationTaskRead:
        assert task_id == 10
        assert user_id == 7
        return EvaluationTaskRead(
            taskId=10,
            status="pending",
            prompt="历史问题",
            createdAt=created_at,
            completedAt=None,
            responses=[],
        )

    monkeypatch.setattr(evaluation_service, "get_task", fake_get_task)

    response = TestClient(app).get("/api/evaluation/tasks/10")

    assert response.status_code == 200
    assert response.json() == {
        "taskId": 10,
        "taskType": "chat",
        "status": "pending",
        "prompt": "历史问题",
            "createdAt": "2026-06-03T12:00:00",
            "completedAt": None,
            "ownerId": None,
            "ownerUsername": "anonymous",
            "visibility": "public",
            "responses": [],
    }


def test_list_evaluation_tasks_returns_paginated_result(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime(2026, 6, 3, 12, 0, 0)

    async def fake_list_tasks(
        _db: object,
        page: int,
        page_size: int,
        user_id: int,
    ) -> EvaluationTaskListRead:
        assert page == 2
        assert page_size == 20
        assert user_id == 7
        return EvaluationTaskListRead(
            items=[
                EvaluationTaskListItemRead(
                    taskId=10,
                    status="completed",
                    prompt="历史问题",
                    createdAt=created_at,
                    completedAt=created_at,
                    responseCount=3,
                )
            ],
            total=21,
            page=2,
            pageSize=20,
        )

    monkeypatch.setattr(evaluation_service, "list_tasks", fake_list_tasks)

    response = TestClient(app).get("/api/evaluation/tasks?page=2&pageSize=20")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "taskId": 10,
                "taskType": "chat",
                "status": "completed",
                "prompt": "历史问题",
                "createdAt": "2026-06-03T12:00:00",
                    "completedAt": "2026-06-03T12:00:00",
                    "responseCount": 3,
                    "ownerId": None,
                    "ownerUsername": "anonymous",
                    "visibility": "public",
                }
        ],
        "total": 21,
        "page": 2,
        "pageSize": 20,
    }


def test_list_response_comments_returns_paginated_result(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime(2026, 6, 6, 10, 30, 0)

    async def fake_list_comments(
        response_id: int,
        _db: object,
        page: int,
        page_size: int,
        user_id: int,
    ) -> CommentListRead:
        assert response_id == 44
        assert page == 2
        assert page_size == 10
        assert user_id == 7
        return CommentListRead(
            items=[
                CommentRead(
                    id=301,
                    responseId=44,
                    userId=0,
                    username="anonymous",
                    content="评论内容",
                    createdAt=created_at,
                    canDelete=True,
                )
            ],
            total=11,
            page=2,
            pageSize=10,
        )

    monkeypatch.setattr(evaluation_service, "list_response_comments", fake_list_comments)

    response = TestClient(app).get("/api/evaluation/responses/44/comments?page=2&pageSize=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["content"] == "评论内容"
    assert response.json()["pageSize"] == 10


def test_create_response_comment_returns_created_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime(2026, 6, 6, 10, 30, 0)

    async def fake_create_comment(
        response_id: int,
        payload: object,
        _db: object,
        user_id: int,
        username: str,
    ) -> CommentRead:
        assert response_id == 44
        assert payload.content == "评论内容"
        assert user_id == 7
        assert username == "test_user"
        return CommentRead(
            id=301,
            responseId=44,
            userId=7,
            username="test_user",
            content=payload.content,
            createdAt=created_at,
            canDelete=True,
        )

    monkeypatch.setattr(evaluation_service, "create_response_comment", fake_create_comment)

    response = TestClient(app).post(
        "/api/evaluation/responses/44/comments",
        json={"content": "  评论内容  "},
    )

    assert response.status_code == 201
    assert response.json()["content"] == "评论内容"


def test_create_response_comment_returns_404_when_response_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create_comment(
        _response_id: int,
        _payload: object,
        _db: object,
        _user_id: int,
        _username: str,
    ) -> None:
        raise EvaluationResponseNotFoundError("模型回答不存在")

    monkeypatch.setattr(evaluation_service, "create_response_comment", fake_create_comment)

    response = TestClient(app).post(
        "/api/evaluation/responses/404/comments",
        json={"content": "评论内容"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "模型回答不存在"}


def test_delete_response_comment_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete_comment(comment_id: int, _db: object, user_id: int) -> None:
        assert comment_id == 301
        assert user_id == 7

    monkeypatch.setattr(evaluation_service, "delete_response_comment", fake_delete_comment)

    response = TestClient(app).delete("/api/evaluation/comments/301")

    assert response.status_code == 204
    assert response.content == b""


def test_delete_response_comment_returns_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete_comment(_comment_id: int, _db: object, _user_id: int) -> None:
        raise EvaluationCommentNotFoundError("评论不存在")

    monkeypatch.setattr(evaluation_service, "delete_response_comment", fake_delete_comment)

    response = TestClient(app).delete("/api/evaluation/comments/404")

    assert response.status_code == 404
    assert response.json() == {"detail": "评论不存在"}


def test_visibility_patch_passes_authenticated_owner_and_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    async def update(task_id, visibility, db, user_id):
        calls.append((task_id, visibility, user_id))
        return EvaluationTaskRead(taskId=task_id, status="completed", prompt="问题", ownerId=user_id,
                                  visibility=visibility, responses=[])
    monkeypatch.setattr(evaluation_service, "update_task_visibility", update)
    client = TestClient(app)
    response = client.patch("/api/evaluation/tasks/12/visibility", json={"visibility": "private"})
    assert response.status_code == 200 and response.json()["visibility"] == "private"
    assert calls == [(12, "private", 7)]
    assert client.patch("/api/evaluation/tasks/12/visibility", json={"visibility": "other"}).status_code == 422
    assert len(calls) == 1


def test_visibility_patch_hides_non_owned_task(monkeypatch: pytest.MonkeyPatch) -> None:
    async def denied(*args):
        raise EvaluationTaskNotFoundError("评测任务不存在")
    monkeypatch.setattr(evaluation_service, "update_task_visibility", denied)
    response = TestClient(app).patch("/api/evaluation/tasks/12/visibility", json={"visibility": "public"})
    assert response.status_code == 404
