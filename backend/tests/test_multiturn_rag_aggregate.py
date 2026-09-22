from decimal import Decimal

import pytest

from app.services.multiturn.scoring import (
    RagEvidenceResult,
    aggregate_rag_reassessments,
    aggregate_rag_assertion_runs,
)
from app.schemas.multiturn import RAGAssertion


def _run(value: Decimal | None, *, unknown: bool = False) -> RagEvidenceResult:
    """构造带完整三维适用性状态的 RAG 复评结果。"""
    states = {
        "faithfulness": "unknown" if unknown else "applicable",
        "citation_correctness": "not_applicable",
        "citation_completeness": "not_applicable",
    }
    return RagEvidenceResult(value, None, None, None, states, Decimal("1") if not unknown else Decimal("0"))


def test_requires_exactly_three_runs_and_two_valid_runs() -> None:
    """恰好三项且至少两项有效。"""
    with pytest.raises(ValueError):
        aggregate_rag_reassessments([_run(Decimal("8")), _run(Decimal("8"))])
    result = aggregate_rag_reassessments([_run(Decimal("8")), None, None])
    assert result.status == "judge_failed"
    assert result.valid_runs == 1


def test_applicability_conflict_is_unstable() -> None:
    """有效复评的逐维适用性不一致时判定不稳定。"""
    other = _run(Decimal("8"))
    other.applicability["faithfulness"] = "not_applicable"
    result = aggregate_rag_reassessments([_run(Decimal("8")), other, None])
    assert result.status == "judge_unstable"


def test_unknown_and_large_range_are_preserved() -> None:
    """未知状态进入 incomplete，维度差异超过二分进入 unstable。"""
    result = aggregate_rag_reassessments([_run(Decimal("1")), _run(Decimal("4")), None])
    assert result.status == "judge_unstable"
    assert result.ranges["faithfulness"] == Decimal("3")

    result = aggregate_rag_reassessments([_run(None, unknown=True), _run(None, unknown=True), None])
    assert result.status == "incomplete"
    assert result.final is None


def test_na_is_excluded_and_zero_score_remains_scored() -> None:
    """不适用维度不参与归一化，零分仍是有效稳定结果。"""
    result = aggregate_rag_reassessments([_run(Decimal("0")), _run(Decimal("0")), None])
    assert result.status == "scored"
    assert result.final == Decimal("0")
    assert result.coverage == Decimal("1")
    assert result.critical_passed is None

    all_na = RagEvidenceResult(None, None, None, None, {
        "faithfulness": "not_applicable",
        "citation_correctness": "not_applicable",
        "citation_completeness": "not_applicable",
    })
    result = aggregate_rag_reassessments([all_na, all_na, None])
    assert result.status == "scored"
    assert result.final is None
    assert result.coverage == Decimal("0")


def test_same_dimension_average_cannot_hide_fact_applicability_conflict() -> None:
    """两个片段事实属性对调时，即便维度均分相同也属于评审分歧。"""
    first = [RAGAssertion(id="a", answer_text="甲", support=Decimal("1")),
             RAGAssertion(id="b", answer_text="乙", needs_citation=False)]
    second = [RAGAssertion(id="a", answer_text="甲", needs_citation=False),
              RAGAssertion(id="b", answer_text="乙", support=Decimal("1"))]
    result = aggregate_rag_assertion_runs([first, second, None], "甲乙", set())
    assert result.status == "judge_unstable"
    assert result.final is None
