"""边界材料保留完整原文与中间修正，不把预算缺口当作覆盖。"""
import pytest

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource, build_judge_messages
from app.services.multiturn.opportunities import opportunity_messages
from app.services.multiturn.report_bridges import build_boundary_plan


def windows() -> tuple[JudgePacket, ...]:
    """构造条件提出、修改与回答分处两窗的固定原文。"""
    sources = tuple(JudgeSource(id=f"{'user' if index % 2 else 'response'}:{index}", turn=index,
        text=text) for index, text in enumerate(("预算100", "已记录100", "改为200", "最终采用200"), 1))
    return tuple(JudgePacket(scope="session", through_turn=4, answer="仅看原文", sources=part,
        checks=(JudgeCheck(id=f"window:{index}:memory", dimension="memory", description="核对记忆"),))
        for index, part in enumerate((sources[:2], sources[2:]), 1))


def test_boundary_material_keeps_complete_order_and_both_prompt_budgets() -> None:
    """足够预算保留条件更新，并同时满足真实提取与审核提示预算。"""
    original = windows()
    plan = build_boundary_plan(original, 10000)
    assert not plan.uncovered and len(plan.materials) == 1
    material = plan.materials[0]
    assert material.sources == (*original[0].sources, *original[1].sources)
    assert material.left_ids == ("user:1", "response:2")
    assert material.right_ids == ("user:3", "response:4")
    assert estimate_tokens(opportunity_messages(material.sources, 4)) <= 10000
    assert all(estimate_tokens(build_judge_messages(window.model_copy(update={"sources": material.sources}))) <= 10000
               for window in original)
    assert plan == build_boundary_plan(original, 10000)


def test_budget_failure_records_boundary_without_truncation() -> None:
    """最小边界也无法容纳时明确记录缺口，不返回裁剪文本。"""
    plan = build_boundary_plan(windows(), 1)
    assert plan.materials == () and plan.uncovered == (1,)
    assert build_boundary_plan(windows()[:1], 10000).materials == ()


def test_partial_boundary_expands_only_contiguous_complete_messages() -> None:
    """预算只允许部分范围时仍保留边界两侧，不跳过中间消息拼接远处材料。"""
    original = windows()
    full = (*original[0].sources, *original[1].sources)
    budget = max(estimate_tokens(opportunity_messages(full, 4)),
        *(estimate_tokens(build_judge_messages(window.model_copy(update={"sources": full}))) for window in original)) - 1
    plan = build_boundary_plan(original, budget)
    assert not plan.uncovered
    selected = plan.materials[0].sources
    assert 2 <= len(selected) < len(full)
    assert original[0].sources[-1] in selected and original[1].sources[0] in selected
    first = full.index(selected[0])
    assert selected == full[first:first + len(selected)]
    assert estimate_tokens(opportunity_messages(selected, 4)) <= budget


def test_duplicate_or_mixed_windows_are_rejected() -> None:
    """拒绝重复、乱序及不同截止范围，避免将无关报告混在一起。"""
    original = windows()
    for invalid in ((original[0], original[0]), tuple(reversed(original)),
                    (original[0], original[1].model_copy(update={"through_turn": 5}))):
        with pytest.raises(ValueError):
            build_boundary_plan(invalid, 10000)
