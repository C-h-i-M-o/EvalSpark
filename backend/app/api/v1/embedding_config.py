from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin
from app.db.session import get_db
from app.models.embedding import EmbeddingConfig
from app.schemas.embedding import EmbeddingConfigPayload, EmbeddingConfigRead
from app.services.embedding_config_service import get_config, payload_values, read_config, runtime_config, save_config
from app.services.rag.clients import EmbeddingClient, RagClientError
from app.services.rag.errors import KnowledgeBaseError

router = APIRouter(dependencies=[Depends(require_admin)])
public_router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("", response_model=EmbeddingConfigRead)
async def get_admin_config(db: AsyncSession = Depends(get_db)) -> EmbeddingConfigRead:
    return read_config(await get_config(db))


@router.put("", response_model=EmbeddingConfigRead)
async def put_config(payload: EmbeddingConfigPayload, db: AsyncSession = Depends(get_db)) -> EmbeddingConfigRead:
    try:
        return await save_config(db, payload)
    except KnowledgeBaseError as error:
        raise HTTPException(error.status_code, detail=str(error)) from None


@router.post("/test")
async def test_config(payload: EmbeddingConfigPayload, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    row = await get_config(db)
    if payload.version != (row.version if row else 0):
        raise HTTPException(409, detail="配置已变化，请刷新后测试")
    values = payload_values(payload, row)
    values["enabled"] = True
    await db.rollback()
    try:
        client = EmbeddingClient(runtime_config(EmbeddingConfig(id=1, version=payload.version, settings_json=values)))
        result = await client.embed(["连接测试"], "document")
        return {"success": True, "message": "连接成功，向量格式与维度校验通过", "dimensions": len(result[0])}
    except (RagClientError, ValueError):
        raise HTTPException(400, detail="Embedding 连接或向量校验失败，请检查地址、密钥、模型和维度") from None


@public_router.get("")
async def get_public_config(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    value = read_config(await get_config(db))
    return {"configured": value.configured, "enabled": value.enabled, "modelName": value.model_name,
        "dimensions": value.dimensions, "chunkUnit": "characters" if value.configured else "tokens"}
