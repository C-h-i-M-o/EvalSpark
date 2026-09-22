"""按完整原文消息构造有界报告窗口，缺少跨窗口关系证据时保留未知。"""
from dataclasses import dataclass

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource, build_judge_messages
from app.services.multiturn.assessment_batches import validate_batches

_DESCRIPTIONS = {
    "goal": "本窗口内明确目标是否完成，未解决的问题是否如实保留；不以回答数量代替完成",
    "memory": "是否有引用早期已知信息的验证机会，若有是否准确保留；没有机会才不适用",
    "consistency": "窗口内是否保持事实和约束一致；用户明确修改旧条件不算矛盾",
    "correction": "是否存在用户纠正或新证据纠错机会，若有是否有效修正；没有机会不适用",
    "efficiency": "对话是否推进任务，是否出现无效重复或反复询问已给信息",
}


@dataclass(frozen=True)
class ReportPlan:
    """总原文快照和固定窗口同存，用于完整覆盖校验与重复正式评审。"""
    packet: JudgePacket
    batches: tuple[JudgePacket, ...]
    limitations: tuple[str, ...]


def _window(sources: tuple[JudgeSource, ...], number: int, through_turn: int) -> JudgePacket:
    """按当前窗口轮次范围固定五维机会，原文角色由稳定消息 ID 标识。"""
    first, last = sources[0].turn, sources[-1].turn
    return JudgePacket(scope="session", through_turn=through_turn, answer="会话报告只依据 sources 中的原文评价。",
        sources=sources, checks=tuple(JudgeCheck(id=f"window:{number}:{dimension}", dimension=dimension,
            description=f"第{first}至{last}轮窗口：{description}。证据不足用unknown，不猜测窗口外内容。")
            for dimension, description in _DESCRIPTIONS.items()))


def build_report_plan(sources: tuple[JudgeSource, ...], through_turn: int, input_budget: int,
                      extra_checks: tuple[JudgeCheck, ...] = (), *,
                      check_sources: dict[str, tuple[str, ...]] | None = None,
                      pair_ready: bool = False) -> ReportPlan:
    """完整消息顺序装箱，每段使用真实提示词预算，绝不静默截断原文。"""
    if not sources or type(input_budget) is not int or input_budget <= 0:
        raise ValueError("报告原文不能为空且预算必须为正整数")
    if any(left.turn > right.turn for left, right in zip(sources, sources[1:])):
        raise ValueError("报告原文必须按轮次排序")
    scoped_sources = check_sources or {}
    source_ids = {source.id for source in sources}
    check_ids = {check.id for check in extra_checks}
    if not scoped_sources.keys() <= check_ids or any(
            not ids or len(set(ids)) != len(ids) or not set(ids) <= source_ids for ids in scoped_sources.values()):
        raise ValueError("要求范围必须引用已存在且不重复的原文来源")
    extra_checks = tuple(check.model_copy(update={"allowed_source_ids": scoped_sources[check.id]})
                         if check.id in scoped_sources else check for check in extra_checks)
    whole = _window(sources, 1, through_turn)
    whole = whole.model_copy(update={"checks": (*whole.checks, *extra_checks)})
    if estimate_tokens(build_judge_messages(whole)) <= input_budget:
        validate_batches(whole, (whole,), input_budget)
        return ReportPlan(whole, (whole,), ())
    # 预留跨窗口未知检查项，防止分段完成后才发现提示词超预算。
    guards = tuple(JudgeCheck(id=f"cross_window:{dimension}", dimension=dimension,
        description="缺少跨窗口联合关系证据，不能声称全局已完成审查。", required_applicability="unknown")
        for dimension in ("goal", "memory", "consistency"))
    batches: list[JudgePacket] = []
    current: tuple[JudgeSource, ...] = ()
    for source in sources:
        trial = _window((*current, source), len(batches) + 1, through_turn)
        reserved = trial.model_copy(update={"checks": (*trial.checks, *guards)})
        # 新版本为完整窗口对预留空间；单条长消息仍使用完整预算，不切碎原文。
        packing_budget = input_budget // 2 if pair_ready and current else input_budget
        if estimate_tokens(build_judge_messages(reserved)) <= packing_budget:
            current = (*current, source)
            continue
        if not current:
            raise ValueError("单条报告原文超过预算，请选择更大的评审上下文容量")
        batches.append(_window(current, len(batches) + 1, through_turn))
        current = (source,)
        single = _window(current, len(batches) + 1, through_turn)
        if estimate_tokens(build_judge_messages(single.model_copy(update={"checks": (*single.checks, *guards)}))) > input_budget:
            raise ValueError("单条报告原文超过预算，请选择更大的评审上下文容量")
    if current:
        batches.append(_window(current, len(batches) + 1, through_turn))
    limitations: tuple[str, ...] = ()
    if len(batches) > 1:
        batches[0] = batches[0].model_copy(update={"checks": (*batches[0].checks, *guards)})
        limitations = ("分窗口读取全部原文，但跨窗口目标、长期记忆和一致性尚未完成联合判断。",)
    for check in extra_checks:
        # 使用要求有效范围内的全部来源，不能按关键词挑选较短或较有利的回答。
        ids = set(scoped_sources.get(check.id, ()))
        selected = tuple(source for source in sources if source.id in ids)
        requirement_batch = whole.model_copy(update={"sources": selected, "checks": (check,)})
        if not selected or estimate_tokens(build_judge_messages(requirement_batch)) > input_budget:
            unknown = check.model_copy(update={"required_applicability": "unknown", "allowed_source_ids": ()})
            requirement_batch = whole.model_copy(update={"sources": (), "checks": (unknown,)})
            if check.required_applicability != "unknown":
                limitations += (f"检查项 {check.id} 的完整有效范围未能装入评审预算，保留未知。",)
        if estimate_tokens(build_judge_messages(requirement_batch)) > input_budget:
            raise ValueError("报告检查项描述超过预算，请选择更大的评审上下文容量")
        batches.append(requirement_batch)
    parent = batches[0].model_copy(update={"sources": sources,
        "checks": tuple(check for batch in batches for check in batch.checks)})
    validate_batches(parent, tuple(batches), input_budget)
    return ReportPlan(parent, tuple(batches), limitations)
