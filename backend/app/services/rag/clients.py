import math
from dataclasses import dataclass
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


def validate_vectors(payload: object, *, expected_count: int) -> list[list[float]]:
    if not isinstance(payload, list):
        raise ValueError("Embedding 响应必须是向量列表")
    if len(payload) != expected_count:
        raise ValueError("Embedding 返回的向量数量与输入不一致")
    vectors: list[list[float]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) != VECTOR_DIMENSIONS:
            raise ValueError("Embedding 向量维度必须为 1024")
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

    async def embed(self, texts: list[str], kind: Literal["query", "document"]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding 输入必须是非空文本列表")
        if kind not in ("query", "document"):
            raise ValueError("Embedding 输入类型不受支持")
        inputs = [
            f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}" if kind == "query" else text
            for text in texts
        ]
        tokenizer = self.tokenizer if self.tokenizer is not None else load_tokenizer(self.config)
        counts = [len(item.ids) for item in tokenizer.encode_batch(inputs, add_special_tokens=True)]
        maximum = self.config.rag_embedding_max_batch_tokens
        if any(count > maximum for count in counts):
            raise ValueError(f"Embedding 单条输入超过 {maximum} Token 上限")

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

    async def _embed_batches(self, client: httpx.AsyncClient, batches: list[list[str]]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in batches:
            try:
                response = await client.post(
                    f"{str(self.config.rag_embedding_url).rstrip('/')}/embed",
                    json={"inputs": batch, "truncate": False, "normalize": True},
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
                vectors.extend(validate_vectors(response.json(), expected_count=len(batch)))
            except ValueError:
                raise RagClientError("embedding_invalid_response", "Embedding 返回的向量格式无效") from None
        return vectors


@dataclass(frozen=True)
class VectorMatch:
    chunk_id: str
    document_id: int
    index_revision: int
    chunk_index: int
    similarity: float


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
        query_vector = validate_vectors([vector], expected_count=1)[0]
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
                collection_name=VECTOR_COLLECTION, query=query_vector, query_filter=query_filter,
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
