import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import app
from app.models.embedding import EmbeddingConfig
from app.schemas.embedding import EmbeddingConfigPayload
from app.services.embedding_config_service import payload_values, read_config, runtime_config
from app.services.rag.clients import EmbeddingClient, RagClientError
from app.services.rag.documents import SourceBlock, split_blocks
from app.schemas.knowledge_base import TextSource
from test_rag_evaluation_store import stored


def payload(**changes: object) -> EmbeddingConfigPayload:
    return EmbeddingConfigPayload.model_validate({"version": 0, "baseUrl": "http://localhost:8080/v1",
        "modelName": "test-embedding", "dimensions": 3, **changes})


def test_config_hides_and_preserves_key_and_rotates_without_changing_collection() -> None:
    row = EmbeddingConfig(id=1, version=1, settings_json=payload_values(payload(apiKey="secret-test"), None))
    assert "secret-test" not in read_config(row).model_dump_json()
    assert read_config(row).has_api_key
    original = runtime_config(row)
    row.settings_json = payload_values(payload(apiKey=""), row)
    assert runtime_config(row).rag_embedding_api_key.get_secret_value() == "secret-test"
    row.settings_json = payload_values(payload(apiKey="rotated"), row)
    assert runtime_config(row).rag_embedding_collection == original.rag_embedding_collection
    row.settings_json = payload_values(payload(modelName="other", clearApiKey=True), row)
    assert runtime_config(row).rag_embedding_collection != original.rag_embedding_collection
    assert not read_config(row).has_api_key


@pytest.mark.parametrize("url", ["https://user:password@example.com/v1", "https://example.com/v1?key=secret", "https://example.com/#secret"])
def test_rejects_credentials_in_url(url: str) -> None:
    with pytest.raises(ValueError):
        payload(baseUrl=url)


def test_admin_routes_require_authentication() -> None:
    with TestClient(app) as client:
        assert client.get("/api/admin/embedding-config").status_code == 401
        assert client.put("/api/admin/embedding-config", json=payload().model_dump(mode="json")).status_code == 401
        assert client.get("/api/embedding-config").status_code == 401


@pytest.mark.asyncio
async def test_compatible_embedding_uses_api_without_local_tokenizer_and_restores_order() -> None:
    config = Settings(_env_file=None, rag_embedding_protocol="openai", rag_embedding_model="test",
        rag_embedding_url="http://local:8080/v1", rag_embedding_api_key=SecretStr("secret-test"), rag_embedding_dimensions=3,
        rag_embedding_query_prefix="query: ")
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer secret-test"
        assert json.loads(request.content) == {"model": "test", "input": ["query: 中文", "query: 问题"], "encoding_format": "float"}
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1, 0]}, {"index": 0, "embedding": [1, 0, 0]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await EmbeddingClient(config, http_client=client).embed(["中文", "问题"], "query") == [[1, 0, 0], [0, 1, 0]]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [[], [{"index": True, "embedding": [1, 0, 0]}], [{"index": 2, "embedding": [1, 0, 0]}],
    [{"index": 0, "embedding": [1, 0]}], [{"index": 0, "embedding": [0, 0, 0]}]])
async def test_invalid_remote_vectors_fail_closed(data: object) -> None:
    config = Settings(_env_file=None, rag_embedding_protocol="openai", rag_embedding_dimensions=3)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data}))) as client:
        with pytest.raises(RagClientError, match="向量格式"):
            await EmbeddingClient(config, http_client=client).embed(["资料"], "document")


def test_character_split_preserves_chinese_sources_without_tokenizer() -> None:
    text = "中文知识库资料。\n" * 100
    chunks = split_blocks([SourceBlock(text, TextSource(line_start=1, line_end=100))], None, 128, 20)
    assert len(chunks) > 1
    assert all(chunk.token_count == len(chunk.text) <= 128 for chunk in chunks)
    assert chunks[0].source.line_start == 1


@pytest.mark.asyncio
async def test_global_change_blocks_active_tasks_then_requires_reindex_and_rejects_stale_save(stored) -> None:
    from sqlalchemy.orm import Session
    from app.models.evaluation import EvaluationTask
    from app.models.knowledge_base import KnowledgeBase
    from app.services.embedding_config_service import save_config
    from app.services.rag.errors import KnowledgeBaseError
    store, context, _, engine = stored
    with Session(engine) as db:
        db.add(EmbeddingConfig(id=1, version=0, settings_json={}))
        db.commit()
    async with store.sessions() as db:
        with pytest.raises(KnowledgeBaseError, match="仍有索引或 RAG"):
            await save_config(db, payload())
    with Session(engine) as db:
        db.get(EvaluationTask, context.task_id).status = "completed"
        db.commit()
    async with store.sessions() as db:
        result = await save_config(db, payload(apiKey="secret-test"))
        assert result.version == 1
        with pytest.raises(KnowledgeBaseError, match="其他管理员"):
            await save_config(db, payload())
    with Session(engine) as db:
        kb = db.get(KnowledgeBase, 1)
        assert kb.status == "reindex_required" and kb.content_revision == 5
    assert "embedding_runtime" not in context.model_dump()


def test_regular_user_cannot_manage_embedding() -> None:
    from app.api.dependencies import get_current_user
    from app.models.user import User
    app.dependency_overrides[get_current_user] = lambda: User(id=1, role="user", username="测试", status="active")
    try:
        with TestClient(app) as client:
            assert client.get("/api/admin/embedding-config").status_code == 403
            assert client.post("/api/admin/embedding-config/test", json=payload().model_dump(mode="json")).status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_delete_cleans_previous_model_collection_without_touching_other_owner() -> None:
    from uuid import uuid4
    from qdrant_client import AsyncQdrantClient
    from app.services.rag.clients import VectorStore
    client = AsyncQdrantClient(":memory:")
    try:
        old = VectorStore(client)
        new = VectorStore(client, Settings(_env_file=None, rag_embedding_collection="rag_chunks_api_test", rag_embedding_dimensions=3))
        await old.ensure_collection()
        await new.ensure_collection()
        await old.upsert(1, 1, 1, 1, [(str(uuid4()), 0)], [[1.0] * 1024])
        await new.upsert(1, 1, 1, 2, [(str(uuid4()), 0)], [[1.0] * 3])
        await new.upsert(2, 2, 2, 1, [(str(uuid4()), 0)], [[1.0] * 3])
        await new.delete(1, 1, 1)
        assert await old.count(1, 1, 1) == await new.count(1, 1, 1) == 0
        assert await new.count(2, 2, 2) == 1
    finally:
        await client.close()
