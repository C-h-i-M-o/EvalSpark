from decimal import Decimal

import pytest

from app.schemas.multiturn import (
    CheckItem,
    DimensionWeight,
    RAGAssertion,
    Reassessment,
    SessionOpportunity,
)
from app.services.multiturn.scoring import (
    aggregate_reassessments,
    score_dialogue,
    score_rag_evidence,
    score_session,
)


def test_dialogue_score_excludes_na_but_preserves_unknown_coverage() -> None:
    """验证不适用项重分配权重，而未知项降低覆盖率。"""
    items = [
        CheckItem(id="s", dimension="solution", rating=4, evidence=["回答" ]),
        CheckItem(id="c", dimension="context", applicability="not_applicable"),
        CheckItem(id="i", dimension="instruction", applicability="unknown"),
        CheckItem(id="e", dimension="expression", rating=2, evidence=["回答" ]),
    ]
    result = score_dialogue(items)
    assert result.dimensions["solution"] == Decimal("10")
    assert result.dimensions["expression"] == Decimal("5")
    assert result.dimensions["context"] is None
    assert result.final is None
    assert result.coverage == Decimal("0.73")
    assert result.applicability["context"] == "not_applicable"
    assert result.applicability["instruction"] == "unknown"


def test_unknown_dimension_prevents_final_and_missing_dimension_is_unknown() -> None:
    """缺少维度不能生成貌似完整的综合分。"""
    result = score_dialogue([CheckItem(id="s", dimension="solution", rating=4, evidence=["回答"])])
    assert result.final is None
    assert result.applicability["context"] == "unknown"


def test_full_score_requires_explicit_na_for_other_dimensions() -> None:
    """明确不适用的维度不阻止其余已评维度形成总分。"""
    result = score_dialogue([
        CheckItem(id="s", dimension="solution", rating=4, evidence=["回答"]),
        CheckItem(id="c", dimension="context", applicability="not_applicable"),
        CheckItem(id="i", dimension="instruction", applicability="not_applicable"),
        CheckItem(id="e", dimension="expression", applicability="not_applicable"),
    ])
    assert result.final == Decimal("10")
    assert result.coverage == Decimal("1")


def test_rejects_invalid_check_items() -> None:
    """严格拒绝布尔档位、越界分数和缺失证据。"""
    with pytest.raises(ValueError):
        score_dialogue([CheckItem(id="x", dimension="solution", rating=True)])
    with pytest.raises(ValueError):
        score_dialogue([CheckItem(id="x", dimension="solution", rating=5)])
    with pytest.raises(ValueError):
        score_dialogue([CheckItem(id="x", dimension="solution", rating=3, evidence=[])])


def test_critical_failure_is_independent_from_score() -> None:
    """关键要求失败独立保留，不被局部高分掩盖。"""
    result = score_dialogue([
        CheckItem(id="x", dimension="solution", rating=4, critical=True, passed=False, evidence=["证据"]),
    ])
    assert result.critical_passed is False
    assert result.final is None  # 其余维度未评估，不伪造完整综合分。


def test_rag_assertions_handle_na_unknown_and_missing_citation() -> None:
    """忠实度、正确引用和缺少引用分别计分。"""
    assertions = [
        RAGAssertion(id="a", needs_citation=True, support=Decimal("1"), citation_support=Decimal("1"), cited_evidence_ids=["e1"], answer_text="答案"),
        RAGAssertion(id="b", needs_citation=True, support=Decimal("0"), cited_evidence_ids=[], answer_text="错误"),
        RAGAssertion(id="c", needs_citation=False, support=None, cited_evidence_ids=[], answer_text="谢谢"),
    ]
    result = score_rag_evidence(assertions, answer="答案[e1]。错误。谢谢。", evidence_ids={"e1"})
    assert result.faithfulness == Decimal("5")
    assert result.citation_completeness == Decimal("5")
    assert result.citation_correctness == Decimal("10")


def test_rag_unknown_citation_support_does_not_count_as_correct() -> None:
    """未知的引用支持不能当作满分或确定不支持。"""
    result = score_rag_evidence([
        RAGAssertion(id="a", needs_citation=True, support=None, cited_evidence_ids=["e1"], answer_text="答案"),
    ], answer="答案[e1]", evidence_ids={"e1"})
    assert result.citation_correctness is None
    assert result.citation_completeness is None


def test_session_score_uses_opportunities_and_unknown() -> None:
    """会话按实际机会评分，并保留未判断的维度。"""
    result = score_session([
        SessionOpportunity(id="g", dimension="goal", rating=4, evidence=["完成"]),
        SessionOpportunity(id="m", dimension="memory", applicability="not_applicable"),
        SessionOpportunity(id="c", dimension="consistency", applicability="unknown"),
        SessionOpportunity(id="r", dimension="correction", rating=2, evidence=["修正"]),
    ])
    assert result.dimensions["goal"] == Decimal("10")
    assert result.final is None
    assert result.coverage == Decimal("0.60")


def test_three_reassessments_need_two_valid_and_low_dimension_spread() -> None:
    """三次复评中至少两个稳定有效结果才能正式评分。"""
    items = [CheckItem(id="x", dimension="solution", rating=4, evidence=["证据"]), *[
        CheckItem(id=name, dimension=name, applicability="not_applicable") for name in ("context", "instruction", "expression")]]
    result = aggregate_reassessments([
        Reassessment(valid=True, run_index=1, items=items),
        Reassessment(valid=True, run_index=2, items=items),
        Reassessment(valid=False, run_index=3, items=[]),
    ])
    assert result.status == "scored"
    assert result.dimensions["solution"] == Decimal("10")


def test_reassessment_conflicting_applicability_is_unstable() -> None:
    """评审对是否适用有分歧时不得强行平均。"""
    result = aggregate_reassessments([
        Reassessment(valid=True, run_index=1, items=[CheckItem(id="x", dimension="solution", applicability="unknown")]),
        Reassessment(valid=True, run_index=2, items=[CheckItem(id="x", dimension="solution", applicability="not_applicable")]),
        Reassessment(valid=False, run_index=3, items=[]),
    ])
    assert result.status == "judge_unstable"


def test_reassessment_requires_three_distinct_runs_and_preserves_zero_score() -> None:
    """拒绝不足三次的正式复评请求。"""
    with pytest.raises(ValueError):
        aggregate_reassessments([Reassessment(valid=True, run_index=1, items=[]), Reassessment(valid=True, run_index=2, items=[])])


def test_zero_score_is_a_valid_formal_score() -> None:
    """合法零分仍是正式成绩，不能因布尔判断被排除。"""
    items = [CheckItem(id=name, dimension=name, rating=0, evidence=["失败"])
             for name in ("solution", "context", "instruction", "expression")]
    result = aggregate_reassessments([
        Reassessment(valid=True, run_index=1, items=items),
        Reassessment(valid=True, run_index=2, items=items),
        Reassessment(valid=False, run_index=3, items=[]),
    ])
    assert result.status == "scored"
    assert result.final == Decimal("0")
