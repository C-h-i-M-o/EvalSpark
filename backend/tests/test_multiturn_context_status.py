"""上下文状态投影不能泄漏内部消息，旧快照不能伪装为未压缩。"""
from app.models.conversation import ConversationContext
from app.services.multiturn.context_status import project_context_status


def test_context_status_only_exposes_metadata_for_both_rag_phases() -> None:
    """普通与两阶段快照保留零值，不返回摘要和系统消息。"""
    snapshot = {"compressed": True, "estimated_tokens": 123, "messages": [{"content": "内部提示词"}],
                "summary": {"content": "摘要正文"}}
    row = ConversationContext(model_config_id=7, covered_through_turn=2, snapshot_json=snapshot)
    assert project_context_status(row)[0].model_dump(by_alias=True) == {"modelConfigId": 7,
        "phase": "chat", "compressed": True, "estimatedTokens": 123, "historyThroughTurn": 2}
    row.snapshot_json = {"rag_requests": {"rewrite": snapshot, "answer": {"compressed": False, "estimated_tokens": 0}}}
    statuses = project_context_status(row)
    assert [item.phase for item in statuses] == ["rewrite", "answer"]
    assert statuses[1].estimated_tokens == 0 and statuses[1].compressed is False


def test_context_status_missing_or_invalid_values_stay_unknown() -> None:
    """旧字段和错误类型不以 bool 或零进行隐式转换。"""
    row = ConversationContext(model_config_id=7, covered_through_turn=0,
                              snapshot_json={"compressed": "false", "estimated_tokens": True})
    status = project_context_status(row)[0]
    assert status.compressed is None and status.estimated_tokens is None
