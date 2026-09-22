"""历史引用必须显式定位，并与本轮新证据一起构成可评分快照。"""
import pytest
import json

from app.schemas.rag import RagEvidence
from app.services.multiturn.rag_references import HistoricalReference, parse_references, merge_reference_evidence
from app.services.multiturn.rag_assessment import _reference_mapping
from app.services.multiturn.store import ConversationError


def evidence(label: str, chunk: str, text: str) -> RagEvidence:
    """构造有文档版本和位置的确定性资料。"""
    return RagEvidence(label=label, document_id=1, document_name="资料", chunk_id=chunk,
        index_revision=1, text=text, similarity=0.9, source={"kind": "text", "lineStart": 1, "lineEnd": 1})


def test_explicit_and_relative_references_do_not_consume_bare_labels() -> None:
    """格式示例不是历史请求，相对轮次和固定轮次保持明确。"""
    assert parse_references("格式使用[S1]", before_turn=4) == ()
    refs = parse_references("解释[T2:S1]、第2轮的[S1]和上一轮[S2]以及刚才的[S3]", before_turn=4)
    assert [(item.turn, item.label) for item in refs] == [(2, "S1"), (3, "S2"), (None, "S3")]


@pytest.mark.parametrize("prompt", ["[T4:S1]", "[T0:S1]", "[T2:S99]", "[T1:S1][T1:S2][T1:S3][T1:S4][T1:S5][T2:S1]"])
def test_future_invalid_and_excessive_references_are_rejected(prompt: str) -> None:
    """不允许未来资料、非法标签或绕过五份证据限额。"""
    with pytest.raises(ValueError):
        parse_references(prompt, before_turn=4)


def test_merge_preserves_requested_history_and_relabels_deduplicated_evidence() -> None:
    """明确历史资料优先，不被本轮前五候选挤掉，也不重复计数。"""
    old = HistoricalReference(turn=1, response_id=10, label="S3", evidence=evidence("S3", "old", "历史原文"))
    fresh = [evidence("S1", "new", "新原文"), evidence("S2", "old", "历史原文")]
    merged = merge_reference_evidence((old,), fresh)
    assert [(item.label, item.text) for item in merged] == [("S1", "历史原文"), ("S2", "新原文")]
    assert old.evidence.label == "S3"
    assert old.as_input(merged)["currentLabel"] == "S1"
    assert old.as_input(merged)["sourceId"] == "response:10:S3"


def test_same_revision_different_text_is_not_silently_overwritten() -> None:
    """同一片段版本出现不同原文时必须显式失败。"""
    old = HistoricalReference(turn=1, response_id=10, label="S1", evidence=evidence("S1", "old", "原文"))
    with pytest.raises(ValueError):
        merge_reference_evidence((old,), [evidence("S1", "old", "被修改的原文")])


@pytest.mark.parametrize("corruption", [None, "text", "label", "source"])
def test_judge_mapping_keeps_all_origins_and_rejects_inconsistent_snapshot(corruption: str | None) -> None:
    """同一片段多次引用保留全部旧来源，原文或标识不一致不能进入评分。"""
    first = HistoricalReference(1, 10, "S3", evidence("S3", "old", "原文"))
    second = HistoricalReference(2, 20, "S2", evidence("S2", "old", "原文"))
    current = merge_reference_evidence((first, second), [])
    references = [item.as_input(current) for item in (first, second)]
    if corruption == "text":
        references[0]["evidence"]["text"] = "伪造原文"
    elif corruption == "label":
        references[0]["currentLabel"] = "S5"
    elif corruption == "source":
        references[0]["sourceId"] = "response:99:S3"
    snapshot = {"rag_requests": {"answer": {"messages": [{"content": json.dumps({"historicalReferences": references})}]}}}
    if corruption:
        with pytest.raises(ConversationError, match="评分历史引用快照无效"):
            _reference_mapping(snapshot, current)
    else:
        mapping = _reference_mapping(snapshot, current)
        assert [(item.reference, item.source_id) for item in mapping["S1"]] == [
            ("T1:S3", "response:10:S3"), ("T2:S2", "response:20:S2")]
