"""容量配置严格校验并在固定隔离库中验证更新和清空语义。"""
import pytest
from pydantic import ValidationError

from app.schemas.model_config import ModelConfigCreate, ModelConfigUpdate
from app.services.model_config_service import ModelConfigService, ModelConfigServiceError


@pytest.mark.parametrize("value", [True, 0, 1, -1, 2.5, "8192"])
def test_context_capacity_rejects_non_positive_integer_or_coercion(value: object) -> None:
    """容量不能由布尔、字符串或小数隐式转换。"""
    with pytest.raises(ValidationError):
        ModelConfigUpdate(contextWindow=value)


@pytest.mark.asyncio
async def test_context_capacity_roundtrip_merge_update_and_clear(rag_sessions) -> None:
    """部分更新保留旧容量，冲突输出被拒绝，显式空值清空容量。"""
    from uuid import uuid4
    service = ModelConfigService()
    async with rag_sessions() as db:
        model = await service.create_config(db, ModelConfigCreate(providerName=f"capacity-{uuid4().hex}",
            displayName="容量测试", modelName="fake", baseUrl="http://model-test:8080/v1",
            contextWindow=8192, maxTokens=1024, apiKey="isolated-capacity-test"))
        assert model.context_window == 8192
        available = next(item for item in await service.list_available_configs(db) if item.id == model.id)
        assert available.context_window == 8192 and available.max_tokens == 1024
        assert set(available.model_dump(by_alias=True)) == {"id", "providerName", "displayName", "modelName", "contextWindow", "maxTokens"}
        unchanged = await service.update_config(db, model.id, ModelConfigUpdate(notes="保持容量"))
        assert unchanged.context_window == 8192
        with pytest.raises(ModelConfigServiceError, match="上下文容量"):
            await service.update_config(db, model.id, ModelConfigUpdate(maxTokens=8192))
        await db.rollback()
        cleared = await service.update_config(db, model.id, ModelConfigUpdate(contextWindow=None))
        assert cleared.context_window is None and cleared.max_tokens == 1024
