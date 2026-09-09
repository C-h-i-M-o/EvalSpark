import math
from dataclasses import dataclass
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Literal

import httpx
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import LocalEntryNotFoundError
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from tokenizers import Tokenizer

from app.core.config import Settings, settings

VECTOR_DIMENSIONS = 1024
VECTOR_COLLECTION = "rag_chunks_v1"
QUERY_INSTRUCTION = "根据问题检索能够支持回答的文档片段"


class RagClientError(RuntimeError):
    """保留稳定错误码和重试分类，不向调用者暴露上游原文。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def validate_vectors(payload: object, *, expected_count: int, dimensions: int = VECTOR_DIMENSIONS) -> list[list[float]]:
    if not isinstance(payload, list):
        raise ValueError("Embedding 响应必须是向量列表")
    if len(payload) != expected_count:
        raise ValueError("Embedding 返回的向量数量与输入不一致")
    vectors: list[list[float]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) != dimensions:
            raise ValueError(f"Embedding 向量维度必须为 {dimensions}")
        if any(type(value) not in (int, float) for value in item):
            raise ValueError("Embedding 向量必须包含有限数值")
        try:
            vector = [float(value) for value in item]
        except OverflowError:
            raise ValueError("Embedding 向量必须包含有限数值") from None
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding 向量必须包含有限数值")
        if not any(vector):
            raise ValueError("Embedding 不能返回零向量")
        vectors.append(vector)
    return vectors


def load_tokenizer(config: Settings = settings) -> Tokenizer:
    return _load_cached_tokenizer(str(config.rag_model_cache_dir), config.rag_embedding_revision)


@lru_cache(maxsize=2)
def _load_cached_tokenizer(cache_dir: str, revision: str) -> Tokenizer:
    try:
        path = hf_hub_download(
            repo_id="Qwen/Qwen3-Embedding-0.6B",
            filename="tokenizer.json",
            revision=revision,
            cache_dir=cache_dir,
            local_files_only=True,
        )
    except (LocalEntryNotFoundError, OSError):
        raise RagClientError("tokenizer_not_ready", "本地分词器缓存尚未就绪", retryable=True) from None
    try:
        tokenizer = Tokenizer.from_file(path)
        # 计数必须基于完整文本，不能继承文件内的截断或补齐设置。
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return tokenizer
    except Exception:
        raise RagClientError("tokenizer_invalid", "本地分词器缓存无法读取，请检查模型缓存") from None


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int | None


class EmbeddingClient:
    def __init__(
        self,
        config: Settings = settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client
        self.tokenizer = tokenizer

    def input_token_count(self, text: str, kind: Literal["query", "document"]) -> int:
        tokenizer = self.tokenizer if self.tokenizer is not None else load_tokenizer(self.config)
        content = f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}" if kind == "query" else text
        return len(tokenizer.encode(content, add_special_tokens=True).ids)

    async def embed(self, texts: list[str], kind: Literal["query", "document"]) -> list[list[float]]:
        return (await self.embed_result(texts, kind)).vectors

    async def embed_result(self, texts: list[str], kind: Literal["query", "document"]) -> EmbeddingResult:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding 输入必须是非空文本列表")
        if kind not in ("query", "document"):
            raise ValueError("Embedding 输入类型不受支持")
        inputs = [self.config.rag_embedding_query_prefix + text if kind == "query" else text for text in texts]
        if self.config.rag_embedding_protocol == "openai":
            # 云端模型的实际 Token 以供应商响应为准，字符限制仅用于请求控制。
            counts = [len(text) for text in inputs]
        else:
            tokenizer = self.tokenizer if self.tokenizer is not None else load_tokenizer(self.config)
            counts = [len(item.ids) for item in tokenizer.encode_batch(inputs, add_special_tokens=True)]
        maximum = self.config.rag_embedding_max_batch_tokens
        if any(count > maximum for count in counts):
            unit = "字符" if self.config.rag_embedding_protocol == "openai" else "Token"
            raise ValueError(f"Embedding 单条输入超过 {maximum} {unit}上限")

        # 全量预检后才开始请求，避免后面的输入超限时前半批已经被处理。
        batches: list[list[str]] = []
        batch: list[str] = []
        batch_tokens = 0
        for text, count in zip(inputs, counts, strict=True):
            if batch and (len(batch) >= self.config.rag_embedding_batch_size or batch_tokens + count > maximum):
                batches.append(batch)
                batch, batch_tokens = [], 0
            batch.append(text)
            batch_tokens += count
        if batch:
            batches.append(batch)

        if self.http_client is not None:
            return await self._embed_batches(self.http_client, batches)
        async with httpx.AsyncClient(
            timeout=self.config.rag_embedding_timeout_seconds, trust_env=False, follow_redirects=False
        ) as client:
            return await self._embed_batches(client, batches)

    async def _embed_batches(self, client: httpx.AsyncClient, batches: list[list[str]]) -> EmbeddingResult:
        vectors: list[list[float]] = []
        input_tokens: int | None = 0
        for batch in batches:
            compatible = self.config.rag_embedding_protocol == "openai"
            path = "embeddings" if compatible else "embed"
            body = {"model": self.config.rag_embedding_model, "input": batch, "encoding_format": "float"} if compatible else {"inputs": batch, "truncate": False, "normalize": True}
            key = self.config.rag_embedding_api_key.get_secret_value()
            try:
                response = await client.post(
                    f"{str(self.config.rag_embedding_url).rstrip('/')}/{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {key}"} if key else {},
                    timeout=self.config.rag_embedding_timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                raise RagClientError(
                    "embedding_rejected", "Embedding 服务拒绝本次请求",
                    retryable=status in (408, 429) or status >= 500,
                ) from None
            except httpx.RequestError:
                raise RagClientError("embedding_unavailable", "Embedding 服务暂时不可用", retryable=True) from None
            try:
                payload = response.json()
                if compatible:
                    usage = payload.get("usage") if isinstance(payload, dict) else None
                    count = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                    total = usage.get("total_tokens") if isinstance(usage, dict) else None
                    if type(count) is int and count >= 0 and type(total) is int and total == count and input_tokens is not None:
                        input_tokens += count
                    else:
                        input_tokens = None
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(data, list) or len(data) != len(batch):
                        raise ValueError("Embedding 响应缺少完整数据")
                    indices = [item.get("index") if isinstance(item, dict) else None for item in data]
                    if any(type(index) is not int for index in indices) or set(indices) != set(range(len(batch))):
                        raise ValueError("Embedding 响应索引无效")
                    payload = [item.get("embedding") for item in sorted(data, key=lambda item: item["index"])]
                vectors.extend(validate_vectors(payload, expected_count=len(batch), dimensions=self.config.rag_embedding_dimensions))
            except ValueError:
                raise RagClientError("embedding_invalid_response", "Embedding 返回的向量格式无效") from None
        return EmbeddingResult(vectors, input_tokens if self.config.rag_embedding_protocol == "openai" else None)


@dataclass(frozen=True)
class VectorMatch:
    chunk_id: str
    document_id: int
    index_revision: int
    chunk_index: int
    similarity: float


class VectorStore:
    """仅处理服务端确定的文档范围；不接受客户端传入任意过滤器。"""

    def __init__(self, client: AsyncQdrantClient, config: Settings = settings) -> None:
        self.client = client
        self.config = config

    @asynccontextmanager
    async def _request(self) -> AsyncIterator[None]:
        try:
            yield
        except UnexpectedResponse as error:
            raise RagClientError("vector_service_rejected", "向量存储服务拒绝本次请求", retryable=error.status_code in (408, 429) or error.status_code >= 500) from None
        except ResponseHandlingException as error:
            raise RagClientError("vector_service_unavailable", "向量存储服务暂时不可用", retryable=isinstance(error.source, httpx.RequestError)) from None

    async def ensure_collection(self) -> None:
        async with self._request():
            if not await self.client.collection_exists(self.config.rag_embedding_collection):
                try:
                    await self.client.create_collection(self.config.rag_embedding_collection, vectors_config=models.VectorParams(size=self.config.rag_embedding_dimensions, distance=models.Distance.COSINE))
                except UnexpectedResponse as error:
                    if error.status_code != 409:
                        raise
            info = await self.client.get_collection(self.config.rag_embedding_collection)
            params = info.config.params.vectors
            if not isinstance(params, models.VectorParams) or params.size != self.config.rag_embedding_dimensions or params.distance != models.Distance.COSINE:
                raise RagClientError("vector_collection_mismatch", "现有向量集合规格不匹配，请检查部署；不会覆盖原集合")
            for name in ("user_id", "knowledge_base_id", "document_id", "index_revision", "chunk_index"):
                await self.client.create_payload_index(
                    self.config.rag_embedding_collection, name,
                    field_schema=models.IntegerIndexParams(type="integer", lookup=True, range=False, is_principal=name == "user_id"),
                    wait=True,
                )

    def _filter(self, user_id: int, kb_id: int, document_id: int, revision: int | None = None, except_revision: int | None = None) -> models.Filter:
        if any(type(value) is not int or value <= 0 for value in (user_id, kb_id, document_id)):
            raise ValueError("向量文档归属无效")
        fields = {"user_id": user_id, "knowledge_base_id": kb_id, "document_id": document_id}
        if revision is not None:
            if revision <= 0:
                raise ValueError("向量文档版本无效")
            fields["index_revision"] = revision
        return models.Filter(
            must=[models.FieldCondition(key=key, match=models.MatchValue(value=value)) for key, value in fields.items()],
            must_not=[models.FieldCondition(key="index_revision", match=models.MatchValue(value=except_revision))] if except_revision is not None else None,
        )

    async def upsert(self, user_id: int, kb_id: int, document_id: int, revision: int, chunks: list[tuple[str, int]], vectors: list[list[float]]) -> None:
        self._filter(user_id, kb_id, document_id, revision)
        validated = validate_vectors(vectors, expected_count=len(chunks), dimensions=self.config.rag_embedding_dimensions)
        points = [models.PointStruct(id=chunk_id, vector=vector, payload={
            "user_id": user_id, "knowledge_base_id": kb_id, "document_id": document_id, "index_revision": revision, "chunk_index": index,
        }) for (chunk_id, index), vector in zip(chunks, validated, strict=True)]
        async with self._request():
            await self.client.upsert(self.config.rag_embedding_collection, points, wait=True, ordering=models.WriteOrdering.STRONG)

    async def count(self, user_id: int, kb_id: int, document_id: int, revision: int | None = None) -> int:
        async with self._request():
            if not await self.client.collection_exists(self.config.rag_embedding_collection):
                return 0
            result = await self.client.count(self.config.rag_embedding_collection, count_filter=self._filter(user_id, kb_id, document_id, revision), exact=True)
            return result.count

    async def delete(self, user_id: int, kb_id: int, document_id: int, *, except_revision: int | None = None) -> None:
        self._filter(user_id, kb_id, document_id)
        async with self._request():
            collections = await self.client.get_collections()
            for item in collections.collections:
                if item.name != VECTOR_COLLECTION and not item.name.startswith("rag_chunks_api_"):
                    continue
                # 新版本只在当前集合保留；历史模型集合中的相同文档也必须清理。
                scope = self._filter(user_id, kb_id, document_id,
                    except_revision=except_revision if item.name == self.config.rag_embedding_collection else None)
                await self.client.delete(item.name, points_selector=scope, wait=True, ordering=models.WriteOrdering.STRONG)
                remaining = await self.client.count(item.name, count_filter=scope, exact=True)
                if remaining.count:
                    raise RagClientError("vector_cleanup_incomplete", "向量清理尚未完成", retryable=True)


class VectorClient:
    def __init__(self, config: Settings = settings, *, client: AsyncQdrantClient | None = None) -> None:
        self.config = config
        self.client = client

    async def search(
        self,
        user_id: int,
        knowledge_base_id: int,
        versions: list[tuple[int, int]],
        vector: list[float],
        limit: int = 5,
    ) -> list[VectorMatch]:
        if user_id <= 0 or knowledge_base_id <= 0 or not 1 <= limit <= 5:
            raise ValueError("检索归属和数量参数无效")
        if not versions:
            return []
        if len(versions) > 100 or any(document <= 0 or revision <= 0 for document, revision in versions):
            raise ValueError("检索文档版本清单无效")
        query_vector = validate_vectors([vector], expected_count=1, dimensions=self.config.rag_embedding_dimensions)[0]
        query_filter = models.Filter(must=[
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
            models.FieldCondition(key="knowledge_base_id", match=models.MatchValue(value=knowledge_base_id)),
            models.Filter(should=[
                models.Filter(must=[
                    models.FieldCondition(key="document_id", match=models.MatchValue(value=document)),
                    models.FieldCondition(key="index_revision", match=models.MatchValue(value=revision)),
                ]) for document, revision in versions
            ]),
        ])
        client = self.client or AsyncQdrantClient(
            url=str(self.config.rag_qdrant_url),
            timeout=math.ceil(self.config.rag_embedding_timeout_seconds),
            check_compatibility=False, trust_env=False,
        )
        try:
            response = await client.query_points(
                collection_name=self.config.rag_embedding_collection, query=query_vector, query_filter=query_filter,
                limit=limit, with_payload=["user_id", "knowledge_base_id", "document_id", "index_revision", "chunk_index"],
                with_vectors=False,
            )
        except UnexpectedResponse as error:
            raise RagClientError(
                "vector_service_rejected", "向量检索服务拒绝本次请求",
                retryable=error.status_code in (408, 429) or error.status_code >= 500,
            ) from None
        except ResponseHandlingException as error:
            raise RagClientError(
                "vector_service_unavailable", "向量检索服务响应异常",
                retryable=isinstance(error.source, httpx.RequestError),
            ) from None
        finally:
            if self.client is None:
                await client.close()

        matches: list[VectorMatch] = []
        for point in response.points:
            payload = point.payload or {}
            document_id, revision, chunk_index = (
                payload.get("document_id"), payload.get("index_revision"), payload.get("chunk_index")
            )
            # 过滤条件与返回元数据都需满足归属，异常结果不能继续读取 MySQL 原文。
            if (
                payload.get("user_id") != user_id or payload.get("knowledge_base_id") != knowledge_base_id
                or type(document_id) is not int or type(revision) is not int or type(chunk_index) is not int
                or (document_id, revision) not in versions or chunk_index < 0 or not math.isfinite(point.score)
            ):
                raise RagClientError("vector_invalid_response", "向量检索结果的归属或版本无效")
            matches.append(VectorMatch(str(point.id), document_id, revision, chunk_index, float(point.score)))
        return sorted(matches, key=lambda item: (-item.similarity, item.chunk_id))
