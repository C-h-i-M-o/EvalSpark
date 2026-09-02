from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_rag_defaults_use_internal_services_and_shared_read_only_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # 默认值测试不应被测试容器的显式 RAG 配置覆盖。
    for name in Settings.model_fields:
        if name.startswith("rag_"):
            monkeypatch.delenv(name.upper(), raising=False)
    settings = Settings(_env_file=None)
    values = settings.model_dump()

    assert str(values.get("rag_embedding_url")) == "http://embedding/"
    assert str(values.get("rag_qdrant_url")) == "http://qdrant:6333/"
    assert str(values.get("rag_redis_url")) == "redis://redis:6379/0"
    assert values.get("rag_model_cache_dir") == Path("/data")
    assert values.get("rag_documents_dir") == Path("/documents")
    assert values.get("rag_embedding_max_batch_tokens") == 2048


@pytest.mark.parametrize(
    "overrides",
    [
        {"rag_embedding_batch_size": 0},
        {"rag_embedding_batch_size": 17},
        {"rag_embedding_max_batch_tokens": 0},
        {"rag_embedding_timeout_seconds": 0},
        {"rag_embedding_revision": "main"},
        {"rag_embedding_url": "file:///etc/passwd"},
        {"rag_qdrant_url": "file:///etc/passwd"},
        {"rag_redis_url": "https://redis/0"},
    ],
)
def test_rejects_unsafe_or_unbounded_rag_configuration(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_worker_uses_json_late_ack_and_bounded_delivery_without_result_backend() -> None:
    from app.worker import celery_app

    assert celery_app.conf.task_default_queue == "rag"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_concurrency == 1
    assert celery_app.conf.task_time_limit == 10800
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 14400
    assert celery_app.conf.result_backend is None
