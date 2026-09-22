from decimal import Decimal

import pytest

from app.schemas.multiturn import CheckItem, RAGAssertion, Reassessment
from app.services.multiturn.scoring import (
    aggregate_reassessments,
    score_dialogue,
    score_rag_evidence,
)


def test_na_dimension_renormalizes_remaining_weights() -> None:
    """验证真正 N/A 的维度会移出分母，并按剩余适用权重重归一。"""
    result = score_dialogue(
        [
            CheckItem(id="s", dimension="solution", rating=4, evidence=["证据"]),
            CheckItem(id="c", dimension="context", applicability="not_applicable"),
            CheckItem(id="i", dimension="instruction", rating=4, evidence=["证据"]),
            CheckItem(id="e", dimension="expression", rating=4, evidence=["证据"]),
        ]
    )

    assert result.final == Decimal("10")
    assert result.coverage == Decimal("1.00")


def test_unknown_rag_support_is_not_renormalized_into_faithfulness() -> None:
    """验证 unknown 支持值不会因排除未知项而把忠实度刷成满分。"""
    result = score_rag_evidence(
        [
            RAGAssertion(id="known", support=Decimal("1"), answer_text="已知"),
            RAGAssertion(id="unknown", support=None, answer_text="未知"),
        ],
        answer="已知未知",
        evidence_ids=set(),
    )

    assert result.faithfulness is None
    assert result.final is None


def test_reassessment_uses_dimension_score_spread_on_zero_to_ten_scale() -> None:
    """验证正式复评按维度 0..10 分差判定，而不是直接比较 0..4 rating。"""
    def item(rating: int) -> CheckItem:
        """构造同一维度的复评检查项。"""
        return CheckItem(id="x", dimension="solution", rating=rating, evidence=["证据"])

    result = aggregate_reassessments(
        [
            Reassessment(valid=True, run_index=1, items=[item(2)]),
            Reassessment(valid=True, run_index=2, items=[item(4)]),
            Reassessment(valid=False, run_index=3, items=[]),
        ]
    )

    assert result.status == "judge_unstable"


def test_consistent_critical_failure_is_scored_and_reported() -> None:
    """验证多次复评一致的关键失败不会被误报为评审不稳定。"""
    def item() -> CheckItem:
        """构造一致失败的关键检查项。"""
        return CheckItem(
            id="x",
            dimension="solution",
            rating=4,
            evidence=["证据"],
            critical=True,
            passed=False,
        )

    result = aggregate_reassessments(
        [
            Reassessment(valid=True, run_index=1, items=[item(), *[
                CheckItem(id=name, dimension=name, applicability="not_applicable")
                for name in ("context", "instruction", "expression")]]),
            Reassessment(valid=True, run_index=2, items=[item(), *[
                CheckItem(id=name, dimension=name, applicability="not_applicable")
                for name in ("context", "instruction", "expression")]]),
            Reassessment(valid=False, run_index=3, items=[]),
        ]
    )

    assert result.status == "scored"
    assert result.critical_passed is False


def test_conflicting_critical_passed_values_are_unstable() -> None:
    """验证关键项 passed 在有效复评间冲突时才标记为不稳定。"""
    def item(passed: bool) -> CheckItem:
        """构造指定关键通过状态的检查项。"""
        return CheckItem(
            id="x",
            dimension="solution",
            rating=4,
            evidence=["证据"],
            critical=True,
            passed=passed,
        )

    result = aggregate_reassessments(
        [
            Reassessment(valid=True, run_index=1, items=[item(True)]),
            Reassessment(valid=True, run_index=2, items=[item(False)]),
            Reassessment(valid=False, run_index=3, items=[]),
        ]
    )

    assert result.status == "judge_unstable"
