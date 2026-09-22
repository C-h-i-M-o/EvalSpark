"""从已保存上下文中投影最小状态，不返回内部消息或摘要正文。"""
from app.models.conversation import ConversationContext
from app.schemas.conversation import ContextStatusRead


def project_context_status(row: ConversationContext) -> list[ContextStatusRead]:
    """兼容普通和 RAG 分阶段快照，非法或缺失元数据保留未知。"""
    snapshot = row.snapshot_json or {}
    phases = snapshot.get("rag_requests")
    records = [(phase, phases[phase]) for phase in ("rewrite", "answer") if phase in phases] if isinstance(phases, dict) else [("chat", snapshot)]
    result = []
    for phase, value in records:
        value = value if isinstance(value, dict) else {}
        count = value.get("estimated_tokens")
        compressed = value.get("compressed")
        result.append(ContextStatusRead(modelConfigId=row.model_config_id, phase=phase,
            compressed=compressed if type(compressed) is bool else None,
            estimatedTokens=count if type(count) is int and count >= 0 else None,
            historyThroughTurn=row.covered_through_turn))
    return result
