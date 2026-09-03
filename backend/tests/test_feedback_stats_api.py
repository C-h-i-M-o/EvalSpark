from importlib import import_module, util

from fastapi.testclient import TestClient
import pytest

from app.api.dependencies import get_current_user
from app.main import app
from app.models.user import User


def _service_module() -> object:
    assert util.find_spec("app.services.feedback_stats_service") is not None
    return import_module("app.services.feedback_stats_service")


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


def test_personal_feedback_stats_uses_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    service_module = _service_module()

    async def fake_get_personal_stats(_db: object, user_id: int, range_name: str) -> dict[str, object]:
        assert user_id == 7
        assert range_name == "7d"
        return {
            "scope": "personal",
            "range": "7d",
            "startAt": "2026-06-12T00:00:00+08:00",
            "endAt": "2026-06-18T12:00:00+08:00",
            "summary": {},
            "myInteractions": {},
            "models": [],
            "trend": [],
        }

    monkeypatch.setattr(service_module.feedback_stats_service, "get_personal_stats", fake_get_personal_stats)

    response = TestClient(app).get("/api/feedback-stats/me?range=7d")

    assert response.status_code == 200
    assert response.json()["scope"] == "personal"
    assert response.json()["range"] == "7d"


def test_normal_user_cannot_access_admin_feedback_stats() -> None:
    response = TestClient(app).get("/api/admin/feedback-stats")

    assert response.status_code == 403


def test_admin_feedback_stats_returns_activity_page(monkeypatch: pytest.MonkeyPatch) -> None:
    service_module = _service_module()
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="admin",
        password_hash="unused",
        role="admin",
        status="active",
    )

    async def fake_get_admin_stats(
        _db: object,
        range_name: str,
        activity_type: str,
        model_config_id: int | None,
        page: int,
        page_size: int,
        *,
        user_id: int,
    ) -> dict[str, object]:
        assert user_id == 1
        assert (range_name, activity_type, model_config_id, page, page_size) == (
            "30d",
            "comment",
            3,
            2,
            10,
        )
        return {
            "scope": "global",
            "range": "30d",
            "startAt": "2026-05-20T00:00:00+08:00",
            "endAt": "2026-06-18T12:00:00+08:00",
            "summary": {},
            "models": [],
            "trend": [],
            "activities": {"items": [], "total": 0, "page": 2, "pageSize": 10},
        }

    monkeypatch.setattr(service_module.feedback_stats_service, "get_admin_stats", fake_get_admin_stats)

    response = TestClient(app).get(
        "/api/admin/feedback-stats",
        params={
            "range": "30d",
            "activityType": "comment",
            "modelConfigId": 3,
            "page": 2,
            "pageSize": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "global"
    assert response.json()["activities"]["page"] == 2


def test_feedback_stats_rejects_unknown_range() -> None:
    response = TestClient(app).get("/api/feedback-stats/me?range=90d")

    assert response.status_code == 422


def test_personal_feedback_stats_requires_authentication() -> None:
    app.dependency_overrides.clear()

    response = TestClient(app).get("/api/feedback-stats/me")

    assert response.status_code == 401
