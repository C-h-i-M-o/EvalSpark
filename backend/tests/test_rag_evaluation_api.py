import pytest
from pydantic import ValidationError

from app.schemas.evaluation import EvaluationTaskCreate
from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.services.evaluation_service import evaluation_service
from app.services.rag.errors import KnowledgeBaseError
from app.services.rag.service import rag_evaluation_service
from app.services.token_quota_service import token_quota_service
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    async def no_check(*args, **kwargs):
        return None
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="本人", password_hash="test", role="user", status="active")
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(token_quota_service, "ensure_can_start", no_check)
    monkeypatch.setattr(evaluation_service, "validate_task_models", no_check)
    yield TestClient(app)
    app.dependency_overrides.clear()


def rag_body():
    return {"taskType": "rag", "prompt": "问题", "modelIds": [1], "judgeModelId": 2,
            "enableJudge": True, "knowledgeBaseId": 1}


def test_rag_request_forces_private_and_requires_knowledge_base_and_idle_judge() -> None:
    body = {"taskType": "rag", "prompt": "问题", "modelIds": [1], "judgeModelId": 2,
            "enableJudge": True, "knowledgeBaseId": 1, "visibility": "public"}
    assert EvaluationTaskCreate(**body).visibility == "public"
    assert EvaluationTaskCreate(**{key: value for key, value in body.items() if key != "visibility"}).visibility == "private"
    for update in ({"knowledgeBaseId": None}, {"enableJudge": False}, {"judgeModelId": None},
                   {"judgeModelId": 1}, {"modelIds": []}, {"modelIds": [1, 1]}, {"prompt": "   "}):
        with pytest.raises(ValidationError):
            EvaluationTaskCreate(**(body | update))
    assert EvaluationTaskCreate(prompt="旧问题", modelIds=[1]).task_type == "chat"
    assert EvaluationTaskCreate(**body).enable_thinking is True
    assert EvaluationTaskCreate(**(body | {"enableThinking": False})).enable_thinking is False


def test_stream_rejects_inaccessible_library_before_ndjson_headers(client, monkeypatch) -> None:
    async def fail(*args):
        raise KnowledgeBaseError("knowledge_base_not_found", "知识库不存在", 404)
    monkeypatch.setattr(rag_evaluation_service, "start", fail)
    response = client.post("/api/evaluation/tasks/stream", json=rag_body())
    assert response.status_code == 404 and response.headers["content-type"] == "application/json"
    assert response.json()["detail"]["code"] == "knowledge_base_not_found"


def test_stream_passes_prepared_run_and_serializes_new_events(client, monkeypatch) -> None:
    import json
    prepared_run = object()
    async def start(payload, db, user_id):
        assert user_id == 1 and payload.visibility == "private"
        return prepared_run
    async def events(payload, db, user_id, username, *, rag_run):
        assert user_id == 1 and username == "本人" and rag_run is prepared_run
        yield {"type": "task_started", "taskType": "rag", "taskId": 1, "prompt": "问题", "modelIds": [1], "total": 1}
        yield {"type": "rag_stage", "modelConfigId": 1, "stage": "rewriting"}
    monkeypatch.setattr(rag_evaluation_service, "start", start)
    monkeypatch.setattr(evaluation_service, "stream_task_events", events)
    response = client.post("/api/evaluation/tasks/stream", json=rag_body())
    assert response.status_code == 200 and "application/x-ndjson" in response.headers["content-type"]
    assert [json.loads(line)["type"] for line in response.text.splitlines()] == ["task_started", "rag_stage"]
