"""构造跨相邻窗口的完整边界材料，保留不能装入预算的缺口。"""
from dataclasses import dataclass

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgePacket, JudgeSource, build_judge_messages
from app.services.multiturn.opportunities import opportunity_messages


@dataclass(frozen=True)
class BoundaryMaterial:
    """固定边界编号与两侧来源，可用于去除窗口内的重复机会。"""
    boundary: int
    sources: tuple[JudgeSource, ...]
    left_ids: tuple[str, ...]
    right_ids: tuple[str, ...]


@dataclass(frozen=True)
class BoundaryPlan:
    """有界材料与未能覆盖的边界分列，不能以空材料冒充检查成功。"""
    materials: tuple[BoundaryMaterial, ...]
    uncovered: tuple[int, ...]


def build_boundary_plan(windows: tuple[JudgePacket, ...], input_budget: int) -> BoundaryPlan:
    """按固定次序扩展相邻完整消息，提取和审核预算均满足才接受。"""
    if type(input_budget) is not int or input_budget <= 0:
        raise ValueError("边界输入预算必须为正整数")
    sources = tuple(source for window in windows for source in window.sources)
    if (any(not window.sources for window in windows)
            or len({source.id for source in sources}) != len(sources)
            or any(a.turn > b.turn for a, b in zip(sources, sources[1:]))
            or len({window.through_turn for window in windows}) > 1):
        raise ValueError("边界窗口必须为同一截止轮次的非空、有序、不重复原文")
    materials: list[BoundaryMaterial] = []
    uncovered: list[int] = []
    for index, (left, right) in enumerate(zip(windows, windows[1:]), 1):
        start, end = len(left.sources) - 1, 1

        def fits(first: int, last: int) -> bool:
            """使用真实提取消息和两侧较大的审核提示开销检查候选范围。"""
            selected = (*left.sources[first:], *right.sources[:last])
            return (estimate_tokens(opportunity_messages(selected, left.through_turn)) <= input_budget
                and all(estimate_tokens(build_judge_messages(window.model_copy(update={"sources": selected}))) <= input_budget
                        for window in (left, right)))

        if not fits(start, end):
            uncovered.append(index)
            continue
        while True:
            expanded = False
            if start > 0 and fits(start - 1, end):
                start -= 1
                expanded = True
            if end < len(right.sources) and fits(start, end + 1):
                end += 1
                expanded = True
            if not expanded:
                break
        left_part, right_part = left.sources[start:], right.sources[:end]
        materials.append(BoundaryMaterial(index, (*left_part, *right_part),
            tuple(source.id for source in left_part), tuple(source.id for source in right_part)))
    return BoundaryPlan(tuple(materials), tuple(uncovered))
