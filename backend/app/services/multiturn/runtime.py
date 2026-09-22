"""按会话快照解析运行模型，凭据实时加载且计费参数保持固定。"""
import json
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.schemas.rag import RagModelSnapshot
from app.services.model_config_service import RuntimeModelConfig, model_config_service
from app.services.multiturn.catalog import model_identity
from app.services.multiturn.store import ConversationError


async def resolve_frozen_model(db: AsyncSession, conversation: Conversation, model_id: int) -> RuntimeModelConfig:
    """拒绝失效或换目标配置，只允许凭据轮换而不改变历史计费口径。"""
    config = conversation.config_json or {}
    identities, snapshots = config.get("identities", {}), config.get("models", [])
    if type(model_id) is not int or not isinstance(identities, dict) or not isinstance(snapshots, list):
        raise ConversationError("conversation_config_invalid", "会话模型配置无效", 409)
    runtime = await model_config_service.resolve_runtime_model(db, model_id)
    if identities.get(str(model_id)) != model_identity(runtime):
        raise ConversationError("conversation_model_changed", "模型调用目标已变化，请新建会话", 409)
    selected = [item for item in snapshots if isinstance(item, dict) and item.get("modelConfigId") == model_id]
    if len(selected) != 1:
        raise ConversationError("conversation_config_invalid", "模型快照缺失或重复", 409)
    saved = RagModelSnapshot.model_validate_json(json.dumps(selected[0]))
    return replace(runtime, max_tokens=saved.max_tokens, temperature=saved.temperature,
        timeout_seconds=saved.timeout_seconds, currency=saved.currency,
        input_price=saved.price_input, output_price=saved.price_output,
        cache_hit_price=saved.price_cache_hit, cache_creation_price=saved.price_cache_creation)
