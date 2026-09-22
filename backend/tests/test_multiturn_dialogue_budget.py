"""单轮超长评审的固定分段与未知降级回归用例。"""

import pytest

from app.services.multiturn.assessment_batches import build_dialogue_batches, validate_batches
from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource, build_judge_messages
from app.services.multiturn.rag_judge import RagJudgeEvidence, build_rag_material


def _packet(*, rag: bool = False) -> JudgePacket:
    """构造包含长历史、当前回答和可选 RAG 资料的单轮父快照。"""
    sources = tuple(JudgeSource(id=f"s{index}", turn=index, text="历史材料" * 80) for index in range(1, 5))
    sources += (JudgeSource(id="current", turn=5, text="当前问题"),)
    material = build_rag_material("当前回答。 [S1]", (
        RagJudgeEvidence(id="doc-1", label="S1", text="支持资料"),
    )) if rag else None
    return JudgePacket(scope="dialogue", through_turn=5, answer=material.answer if material else "当前回答。",
                       sources=sources, checks=(
                           JudgeCheck(id="solution", dimension="solution", description="解决问题"),
                           JudgeCheck(id="expression", dimension="expression", description="表达清晰"),
                       ), rag=material)


def test_long_history_keeps_parent_and_degrades_only_unfittable_check() -> None:
    """长历史无法装入时保留父原文，并将对应检查固定为未知。"""
    parent = _packet()
    expression_budget = estimate_tokens(build_judge_messages(parent.model_copy(update={"checks": (parent.checks[1],),
                                                                                         "sources": (parent.sources[-1],)})))
    batches = build_dialogue_batches(parent, expression_budget)
    assert parent.sources[0].text == "历史材料" * 80
    assert batches[0].checks[0].required_applicability == "unknown"
    assert batches[1].checks[0].required_applicability is None


@pytest.mark.parametrize("field", ["answer", "sources", "checks"])
def test_dialogue_child_tampering_is_rejected(field: str) -> None:
    """子包答案、来源或检查项被篡改时不能通过规范校验。"""
    parent = _packet()
    budget = estimate_tokens(build_judge_messages(parent))
    batches = build_dialogue_batches(parent, budget)
    child = batches[0]
    if field == "answer":
        child = child.model_copy(update={"answer": "伪造回答"})
    elif field == "sources":
        child = child.model_copy(update={"sources": ()})
    else:
        child = child.model_copy(update={"checks": (parent.checks[1],)})
    with pytest.raises(ValueError):
        validate_batches(parent, (child, *batches[1:]), budget)


def test_rag_material_remains_in_parent_when_rag_package_does_not_fit() -> None:
    """RAG 资料超预算时父快照保留资料，派生包不携带 RAG 评审。"""
    parent = _packet(rag=True)
    parent = parent.model_copy(update={"rag": build_rag_material(parent.answer, (
        RagJudgeEvidence(id="doc-1", label="S1", text="完整资料" * 5000),))})
    budget = estimate_tokens(build_judge_messages(parent.model_copy(update={"checks": (parent.checks[1],),
        "rag": None, "sources": (parent.sources[-1],)})))
    batches = build_dialogue_batches(parent, budget)
    assert parent.rag is not None
    assert all(item.rag is None for item in batches)


def test_rag_fits_once_without_repeating_full_history() -> None:
    """历史很长但本轮资料可容纳时，每组只有表达包携带完整资料。"""
    parent = _packet(rag=True)
    budget = estimate_tokens(build_judge_messages(parent.model_copy(update={"checks": (parent.checks[1],),
        "sources": (parent.sources[-1],)})))
    batches = build_dialogue_batches(parent, budget)
    assert sum(item.rag is not None for item in batches) == 1
    assert batches[1].rag == parent.rag
