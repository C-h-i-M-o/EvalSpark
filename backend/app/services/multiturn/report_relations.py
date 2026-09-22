"""规划有界的远距离原文对，并规范合并具有相同证据锚点的机会。"""
from dataclasses import dataclass, replace

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgePacket, build_judge_messages
from app.services.multiturn.opportunities import Opportunity, OpportunityResult, opportunity_messages
from app.services.multiturn.report_bridges import BoundaryMaterial


@dataclass(frozen=True)
class RelationPlan:
    """完整窗口对和未覆盖原因分列，不把调用上限当成全局覆盖成功。"""
    materials: tuple[BoundaryMaterial, ...]
    limitations: tuple[str, ...]


def build_relation_plan(windows: tuple[JudgePacket, ...], budget: int) -> RelationPlan:
    """按窗口距离和位置固定非相邻原文对，最多登记三十二次额外提取。"""
    if type(budget) is not int or budget <= 0:
        raise ValueError("远距离评审预算必须为正整数")
    materials: list[BoundaryMaterial] = []
    oversized = 0
    limited = 0
    # 越远的关联越容易在局部窗口遗漏，优先固定最远窗口对。
    for distance in range(len(windows) - 1, 1, -1):
        for start in range(len(windows) - distance):
            left, right = windows[start], windows[start + distance]
            selected = (*left.sources, *right.sources)
            if (estimate_tokens(opportunity_messages(selected, left.through_turn)) > budget
                    or any(estimate_tokens(build_judge_messages(window.model_copy(update={"sources": selected}))) > budget
                           for window in (left, right))):
                oversized += 1
                continue
            if len(materials) >= 32:
                limited += 1
                continue
            materials.append(BoundaryMaterial(start + 1, selected,
                tuple(source.id for source in left.sources), tuple(source.id for source in right.sources)))
    limitations = []
    if oversized:
        limitations.append(f"{oversized} 个非相邻窗口对的完整原文超过预算，远距离关联覆盖未知。")
    if limited:
        limitations.append(f"{limited} 个非相邻窗口对超过32次额外提取上限，远距离关联覆盖未知。")
    return RelationPlan(tuple(materials), tuple(limitations))


def consolidate_discoveries(discoveries: tuple[OpportunityResult, ...]) -> OpportunityResult:
    """仅合并相同标识和精确锚点的机会，保留全部说明及辅助原文依据。"""
    merged: dict[str, Opportunity] = {}
    descriptions: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for discovery in discoveries:
        if discovery.status == "failed":
            raise ValueError("失败的提取不能参与机会汇总")
        unresolved.extend(discovery.unresolved)
        for item in discovery.opportunities:
            previous = merged.get(item.id)
            if previous is None:
                merged[item.id] = item
                descriptions[item.id] = [item.description]
                continue
            if (previous.dimension, previous.trigger, previous.target) != (item.dimension, item.trigger, item.target):
                raise ValueError("相同机会标识对应不同证据锚点")
            references = { (ref.source_id, ref.turn, ref.quote): ref
                for ref in (*previous.supporting, *item.supporting) }
            if item.description not in descriptions[item.id]:
                descriptions[item.id].append(item.description)
            merged[item.id] = replace(previous, description="；".join(descriptions[item.id]),
                supporting=tuple(references.values()))
    status = "ready" if all(item.status == "ready" for item in discoveries) else "incomplete"
    return OpportunityResult(status, tuple(merged.values()), tuple(dict.fromkeys(unresolved)))
