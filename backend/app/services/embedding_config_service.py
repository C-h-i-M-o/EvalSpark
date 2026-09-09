"""全局配置版本、服务端凭据与索引失效管理。"""

import hashlib
import json

from pydantic import AnyHttpUrl, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import load_api_key, store_api_key
from app.core.config import Settings, settings
from app.models.embedding import EmbeddingConfig
from app.models.evaluation import EvaluationTask
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, RagJob
from app.schemas.embedding import EmbeddingConfigPayload, EmbeddingConfigRead
from app.services.rag.errors import KnowledgeBaseError


async def get_config(db: AsyncSession, *, lock: bool = False) -> EmbeddingConfig | None:
    query = select(EmbeddingConfig).where(EmbeddingConfig.id == 1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


def read_config(row: EmbeddingConfig | None) -> EmbeddingConfigRead:
    values = row.settings_json if row else {}
    return EmbeddingConfigRead(version=row.version if row else 0, configured=bool(values),
        base_url=str(values.get("base_url", "http://embedding:80/v1")),
        has_api_key=bool(values.get("api_key")), model_name=str(values.get("model_name", "Qwen/Qwen3-Embedding-0.6B")),
        dimensions=int(values.get("dimensions", 1024)), query_prefix=str(values.get("query_prefix", "")),
        timeout_seconds=int(values.get("timeout_seconds", 60)), batch_size=int(values.get("batch_size", 16)),
        max_input_characters=int(values.get("max_input_characters", 2048)), enabled=bool(values.get("enabled", False)))


def runtime_config(row: EmbeddingConfig | None, *, allow_disabled: bool = False) -> Settings:
    if row is None or not row.settings_json:
        return settings
    values = row.settings_json
    if not values.get("enabled") and not allow_disabled:
        raise KnowledgeBaseError("embedding_disabled", "管理员尚未启用 Embedding 服务", 409)
    semantic = {key: values[key] for key in ("base_url", "model_name", "dimensions", "query_prefix")}
    fingerprint = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()[:24]
    return settings.model_copy(update={
        "rag_embedding_url": AnyHttpUrl(str(values["base_url"])),
        "rag_embedding_protocol": "openai", "rag_embedding_model": str(values["model_name"]),
        "rag_embedding_api_key": SecretStr(load_api_key(str(values.get("api_key") or ""))),
        "rag_embedding_dimensions": int(values["dimensions"]),
        "rag_embedding_query_prefix": str(values["query_prefix"]),
        "rag_embedding_timeout_seconds": int(values["timeout_seconds"]),
        "rag_embedding_batch_size": int(values["batch_size"]),
        "rag_embedding_max_batch_tokens": int(values["max_input_characters"]),
        "rag_embedding_collection": f"rag_chunks_api_{fingerprint}",
    })


def payload_values(payload: EmbeddingConfigPayload, row: EmbeddingConfig | None) -> dict[str, object]:
    values = payload.model_dump(exclude={"version", "api_key", "clear_api_key"}, mode="json")
    values["base_url"] = str(payload.base_url).rstrip("/")
    values["model_name"] = payload.model_name.strip()
    key = payload.api_key.get_secret_value().strip() if payload.api_key else ""
    values["api_key"] = store_api_key(key) if key else (row.settings_json.get("api_key") if row else None)
    if payload.clear_api_key:
        values["api_key"] = None
    return values


async def save_config(db: AsyncSession, payload: EmbeddingConfigPayload) -> EmbeddingConfigRead:
    await db.rollback()
    async with db.begin():
        row = await get_config(db, lock=True)
        if row is None:
            raise KnowledgeBaseError("embedding_migration_required", "请先完成 Embedding 配置数据库迁移", 409)
        if payload.version != row.version:
            raise KnowledgeBaseError("embedding_config_conflict", "配置已被其他管理员修改，请刷新后重试", 409)
        values = payload_values(payload, row)
        semantic = ("base_url", "model_name", "dimensions", "query_prefix")
        changed = any(values[key] != row.settings_json.get(key) for key in semantic)
        # 所有知识库写操作先锁库；此处持有同样的锁后再检查作业，阻止并发上传绕过检查。
        libraries = list((await db.scalars(select(KnowledgeBase).where(KnowledgeBase.status != "deleted")
            .order_by(KnowledgeBase.id).with_for_update())).all())
        if await db.scalar(select(RagJob.id).where(RagJob.status.in_(("queued", "running"))).limit(1)) or await db.scalar(
            select(EvaluationTask.id).where(EvaluationTask.task_type == "rag", EvaluationTask.status == "pending").limit(1)):
            raise KnowledgeBaseError("embedding_config_busy", "仍有索引或 RAG 任务，请完成后保存配置", 409)
        if changed:
            populated = set((await db.scalars(select(KnowledgeDocument.knowledge_base_id)
                .where(KnowledgeDocument.status != "deleted").distinct())).all())
            for kb in libraries:
                if kb.id in populated and kb.status != "deleting":
                    kb.status, kb.error_code = "reindex_required", None
                    kb.content_revision += 1
        row.settings_json, row.version = values, row.version + 1
        return read_config(row)
