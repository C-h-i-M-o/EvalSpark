"""验证回答引用错误与评审结构错误采用不同处理方式。"""
from decimal import Decimal

import pytest

from app.schemas.multiturn import RAGAssertion
from app.services.multiturn.scoring import score_rag_evidence


def test_invented_answer_reference_is_zero_not_invalid_judge() -> None:
    """实际出现的假引用参与扣分，不使已知资料支持失效。"""
    result = score_rag_evidence([
        RAGAssertion(id="a", answer_text="三个月", support=Decimal("1"),
                     invalid_cited_evidence_ids=["S99"]),
    ], "三个月[S99]", {"S1"})
    assert result.faithfulness == Decimal("10")
    assert result.citation_correctness == Decimal("0")
    assert result.citation_completeness == Decimal("0")
    assert result.final == Decimal("5")


def test_valid_and_invalid_references_each_count_once() -> None:
    """正确引用可以覆盖断言，但多余假引用仍降低引用正确性。"""
    result = score_rag_evidence([
        RAGAssertion(id="a", answer_text="三个月", support=Decimal("1"),
                     citation_support=Decimal("1"), cited_evidence_ids=["S1"],
                     invalid_cited_evidence_ids=["S99"]),
    ], "三个月[S1][S99]", {"S1"})
    assert result.citation_correctness == Decimal("5")
    assert result.citation_completeness == Decimal("10")
    assert result.final == Decimal("8.50")


@pytest.mark.parametrize("invalid", [["S1"], ["S88"], ["S99", "S99"]])
def test_forged_or_duplicate_invalid_reference_is_rejected(invalid: list[str]) -> None:
    """不能伪造错误关系、把已有来源当假引用或重复扣分。"""
    with pytest.raises(ValueError):
        score_rag_evidence([
            RAGAssertion(id="a", answer_text="三个月", support=Decimal("1"),
                         invalid_cited_evidence_ids=invalid),
        ], "三个月[S1][S99]", {"S1"})
