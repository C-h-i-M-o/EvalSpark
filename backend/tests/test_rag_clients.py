import json
from pathlib import Path

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from tokenizers import Tokenizer, models, pre_tokenizers

from app.core.config import Settings
from app.services.rag.clients import (
    EmbeddingClient,
    RagClientError,
    VectorClient,
    load_tokenizer,
    validate_vectors,
)


@pytest.fixture
def tokenizer() -> Tokenizer:
    # 小词表只验证客户端分批和计数行为；真实 Qwen 分词另做缓存实测。
    value = Tokenizer(models.WordLevel({"[UNK]": 0, "资料": 1, "问题": 2}, unk_token="[UNK]"))
    value.pre_tokenizer = pre_tokenizers.Whitespace()
    return value


def test_vector_validation_returns_numeric_vectors() -> None:
    assert validate_vectors([[1] + [0] * 1023], expected_count=1) == [[1.0] + [0.0] * 1023]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([[0.1] * 1023], "向量维度"),
        ([], "向量数量"),
        ({"embedding": [0.1] * 1024}, "向量列表"),
        ([[float("nan")] + [0.1] * 1023], "有限数值"),
        ([[float("inf")] + [0.1] * 1023], "有限数值"),
        ([[True] + [0.1] * 1023], "有限数值"),
        ([["1"] + [0.1] * 1023], "有限数值"),
        ([[10 ** 400] + [0.1] * 1023], "有限数值"),
        ([[0.0] * 1024], "零向量"),
    ],
)
def test_rejects_unusable_vectors(payload: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_vectors(payload, expected_count=1)


@pytest.mark.asyncio
async def test_query_instruction_is_not_added_to_documents(tokenizer: Tokenizer) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[[1.0] + [0.0] * 1023])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = EmbeddingClient(Settings(_env_file=None), http_client=http, tokenizer=tokenizer)
        query_vectors = await client.embed(["问题"], kind="query")
        document_vectors = await client.embed(["资料"], kind="document")

    assert requests[0].url == httpx.URL("http://embedding/embed")
    assert json.loads(requests[0].content) == {
        "inputs": ["Instruct: 根据问题检索能够支持回答的文档片段\nQuery: 问题"],
        "truncate": False,
        "normalize": True,
    }
    assert json.loads(requests[1].content) == {
        "inputs": ["资料"], "truncate": False, "normalize": True
    }
    assert query_vectors == document_vectors == [[1.0] + [0.0] * 1023]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("batch_size", "batch_tokens", "texts", "expected_batches"),
    [
        (2, 8192, ["资料", "问题", "资料"], [["资料", "问题"], ["资料"]]),
        (16, 3, ["资料 问题", "问题 资料", "资料"], [["资料 问题"], ["问题 资料", "资料"]]),
    ],
)
async def test_batches_by_both_count_and_tokens_without_reordering(
    tokenizer: Tokenizer,
    batch_size: int,
    batch_tokens: int,
    texts: list[str],
    expected_batches: list[list[str]],
) -> None:
    batches: list[list[str]] = []
    vector_index = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal vector_index
        batch: list[str] = json.loads(request.content)["inputs"]
        batches.append(batch)
        vectors = []
        for _ in batch:
            vector_index += 1
            vectors.append([float(vector_index)] + [0.0] * 1023)
        return httpx.Response(200, json=vectors)

    settings = Settings(
        _env_file=None,
        rag_embedding_batch_size=batch_size,
        rag_embedding_max_batch_tokens=batch_tokens,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await EmbeddingClient(settings, http_client=http, tokenizer=tokenizer).embed(
            texts, kind="document"
        )

    assert batches == expected_batches
    assert [vector[0] for vector in result] == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_validates_all_inputs_before_sending_any_batch(tokenizer: Tokenizer) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("输入超限时不应发送部分批次")

    settings = Settings(_env_file=None, rag_embedding_batch_size=1, rag_embedding_max_batch_tokens=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = EmbeddingClient(settings, http_client=http, tokenizer=tokenizer)
        with pytest.raises(ValueError, match="Token"):
            await client.embed(["资料", "问题 资料 问题"], kind="document")
        with pytest.raises(ValueError, match="Token"):
            await client.embed(["问题"], kind="query")


@pytest.mark.asyncio
@pytest.mark.parametrize("texts", [[], [""], ["   "]])
async def test_rejects_empty_inputs(tokenizer: Tokenizer, texts: list[str]) -> None:
    with pytest.raises(ValueError, match="非空"):
        await EmbeddingClient(Settings(_env_file=None), tokenizer=tokenizer).embed(texts, kind="document")


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "retryable"), [(400, False), (429, True), (503, True)])
async def test_embedding_errors_hide_upstream_body_and_classify_retry(
    tokenizer: Tokenizer, status: int, retryable: bool
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="私密凭据与原文不应进入业务错误")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = EmbeddingClient(Settings(_env_file=None), http_client=http, tokenizer=tokenizer)
        with pytest.raises(RagClientError) as error:
            await client.embed(["资料"], kind="document")

    assert error.value.retryable is retryable
    assert "私密凭据" not in str(error.value)
    assert error.value.code.startswith("embedding_")


def test_loads_only_the_pinned_tokenizer_from_local_cache(tmp_path: Path, tokenizer: Tokenizer) -> None:
    revision = "a" * 40
    snapshot = tmp_path / "models--Qwen--Qwen3-Embedding-0.6B" / "snapshots" / revision
    snapshot.mkdir(parents=True)
    tokenizer.save(str(snapshot / "tokenizer.json"))
    settings = Settings(_env_file=None, rag_model_cache_dir=tmp_path, rag_embedding_revision=revision)

    cached = load_tokenizer(settings)

    assert cached.encode("资料 问题", add_special_tokens=False).ids == [1, 2]


def test_missing_cache_fails_locally_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        pytest.fail("分词器缓存缺失时不允许联网下载")

    monkeypatch.setattr(httpx.Client, "send", reject_network)
    settings = Settings(_env_file=None, rag_model_cache_dir=tmp_path)
    with pytest.raises(RagClientError) as error:
        load_tokenizer(settings)
    assert error.value.code == "tokenizer_not_ready"
    assert str(tmp_path) not in str(error.value)


@pytest.mark.asyncio
async def test_vector_search_sends_tenant_and_exact_document_version_filter() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "result": {"points": [{
                "id": "1978f15c-6b17-488d-9a90-6ab94feb66f1", "version": 1, "score": 0.9,
                "payload": {"user_id": 7, "knowledge_base_id": 12, "document_id": 15,
                            "index_revision": 3, "chunk_index": 0}, "vector": None,
            }]}, "status": "ok", "time": 0.001,
        })

    sdk = AsyncQdrantClient(
        url="http://qdrant:6333", check_compatibility=False, transport=httpx.MockTransport(handle)
    )
    try:
        matches = await VectorClient(Settings(_env_file=None), client=sdk).search(
            user_id=7, knowledge_base_id=12, versions=[(15, 3), (16, 4)], vector=[1.0] + [0.0] * 1023
        )
    finally:
        await sdk.close()

    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/collections/rag_chunks_v1/points/query"
    assert body["limit"] == 5
    assert body.get("score_threshold") is None
    assert body["filter"] == {"must": [
        {"key": "user_id", "match": {"value": 7}},
        {"key": "knowledge_base_id", "match": {"value": 12}},
        {"should": [
            {"must": [{"key": "document_id", "match": {"value": 15}},
                      {"key": "index_revision", "match": {"value": 3}}]},
            {"must": [{"key": "document_id", "match": {"value": 16}},
                      {"key": "index_revision", "match": {"value": 4}}]},
        ]},
    ]}
    assert len(matches) == 1
    assert matches[0].chunk_id == "1978f15c-6b17-488d-9a90-6ab94feb66f1"
    assert matches[0].document_id == 15
    assert matches[0].similarity == 0.9


@pytest.mark.asyncio
async def test_empty_version_manifest_never_queries_the_whole_collection() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("空的授权版本清单不应触发全库检索")

    sdk = AsyncQdrantClient(
        url="http://qdrant:6333", check_compatibility=False, transport=httpx.MockTransport(handle)
    )
    try:
        assert await VectorClient(Settings(_env_file=None), client=sdk).search(
            user_id=7, knowledge_base_id=12, versions=[], vector=[1.0] + [0.0] * 1023
        ) == []
    finally:
        await sdk.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", ["非 JSON", "[]", "[[1]]", json.dumps([[10 ** 400] + [0.0] * 1023])],
    ids=["invalid-json", "wrong-count", "wrong-dimensions", "numeric-overflow"],
)
async def test_embedding_boundary_rejects_invalid_upstream_vectors(tokenizer: Tokenizer, body: str) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))) as http:
        with pytest.raises(RagClientError) as error:
            await EmbeddingClient(Settings(_env_file=None), http_client=http, tokenizer=tokenizer).embed(
                ["资料"], kind="document"
            )
    assert error.value.code == "embedding_invalid_response"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_embedding_timeout_is_retryable_and_hides_request_details(tokenizer: Tokenizer) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("不应回显的私密请求", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(RagClientError) as error:
            await EmbeddingClient(Settings(_env_file=None), http_client=http, tokenizer=tokenizer).embed(
                ["资料"], kind="document"
            )
    assert error.value.code == "embedding_unavailable"
    assert error.value.retryable is True
    assert "私密" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"user_id": 8}, {"knowledge_base_id": 13}, {"document_id": 16},
    {"index_revision": 2}, {"chunk_index": -1}, {"chunk_index": "0"},
])
async def test_vector_boundary_rejects_foreign_or_stale_metadata(override: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "user_id": 7, "knowledge_base_id": 12, "document_id": 15, "index_revision": 3, "chunk_index": 0,
    }
    payload.update(override)
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"points": [
            {"id": 1, "version": 1, "score": 0.9, "payload": payload},
        ]}, "status": "ok", "time": 0.001})

    sdk = AsyncQdrantClient(url="http://qdrant:6333", check_compatibility=False, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RagClientError) as error:
            await VectorClient(Settings(_env_file=None), client=sdk).search(
                7, 12, [(15, 3)], [1.0] + [0.0] * 1023,
            )
        assert error.value.code == "vector_invalid_response"
        assert error.value.retryable is False
    finally:
        await sdk.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "retryable"), [(400, False), (503, True), (None, True)])
async def test_vector_errors_are_classified_and_redacted(status: int | None, retryable: bool) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ReadTimeout("私密原文", request=request)
        return httpx.Response(status, text="私密原文")

    sdk = AsyncQdrantClient(url="http://qdrant:6333", check_compatibility=False, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RagClientError) as error:
            await VectorClient(Settings(_env_file=None), client=sdk).search(
                7, 12, [(15, 3)], [1.0] + [0.0] * 1023,
            )
        assert error.value.retryable is retryable
        assert "私密" not in str(error.value)
    finally:
        await sdk.close()
