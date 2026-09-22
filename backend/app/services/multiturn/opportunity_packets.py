"""将有来源的具体机会转换为固定评审材料，保留完整区间内的条件变更。"""
from dataclasses import dataclass

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource, build_judge_messages
from app.services.multiturn.opportunities import OpportunityResult


@dataclass(frozen=True)
class OpportunityPackets:
    """机会评审分段及装配限制，不代表全局机会发现覆盖率。"""
    batches: tuple[JudgePacket, ...]
    limitations: tuple[str, ...]


def build_opportunity_packets(sources: tuple[JudgeSource, ...], discovery: OpportunityResult, *,
                              through_turn: int, input_budget: int) -> OpportunityPackets:
    """每个机会装入完整时序区间，超预算保持未知，禁止只截取有利引文。"""
    if any(type(value) is not int or value <= 0 for value in (through_turn, input_budget)):
        raise ValueError("截止轮次和输入预算必须为正整数")
    source_map = {source.id: source for source in sources}
    if (not sources or len(source_map) != len(sources)
            or any(source.turn > through_turn for source in sources)
            or any(left.turn > right.turn for left, right in zip(sources, sources[1:]))):
        raise ValueError("原文快照为空、重复、乱序或包含未来材料")
    if discovery.status == "failed":
        raise ValueError("失败的机会提取不能形成评审材料")
    if len({item.id for item in discovery.opportunities}) != len(discovery.opportunities):
        raise ValueError("机会快照包含重复标识")
    batches: list[JudgePacket] = []
    limitations = list(discovery.unresolved)
    if discovery.status == "incomplete":
        limitations.append("语义机会提取不完整，不能声称全部机会已检查。")
    for item in discovery.opportunities:
        references = (item.trigger, item.target, *item.supporting)
        for reference in references:
            source = source_map.get(reference.source_id)
            if (source is None or source.turn != reference.turn or not reference.quote.strip()
                    or reference.quote not in source.text):
                raise ValueError("机会引用与原文快照不一致")
        first = min(reference.turn for reference in references)
        last = item.target.turn
        if (not item.target.source_id.startswith("response:") or any(ref.turn > last for ref in references)
                or (item.dimension in ("memory", "consistency") and item.trigger.turn >= last)):
            raise ValueError("机会快照的目标角色或时序无效")
        selected = tuple(source for source in sources if first <= source.turn <= last)
        check = JudgeCheck(id=item.id, dimension=item.dimension,
            description=f"{item.description}。触发依据：{item.trigger.source_id}：{item.trigger.quote}；"
                f"检查目标：{item.target.source_id}：{item.target.quote}。"
                "结合区间内全部用户修改和回答判断此机会是否成立及目标表现；合法修改不算遗忘。",
            allowed_source_ids=tuple(source.id for source in selected),
            required_source_ids=tuple(dict.fromkeys((item.trigger.source_id, item.target.source_id))))
        packet = JudgePacket(scope="session", through_turn=through_turn,
            answer="会话报告只依据 sources 中的原文评价。", sources=selected, checks=(check,))
        if estimate_tokens(build_judge_messages(packet)) > input_budget:
            check = check.model_copy(update={"required_applicability": "unknown", "allowed_source_ids": (),
                                             "required_source_ids": ()})
            packet = packet.model_copy(update={"sources": (), "checks": (check,)})
            limitations.append(f"机会 {item.id} 的完整原文区间超过评审预算，保留未知。")
            if estimate_tokens(build_judge_messages(packet)) > input_budget:
                raise ValueError("机会检查项说明超过评审预算")
        batches.append(packet)
    return OpportunityPackets(tuple(batches), tuple(limitations))
