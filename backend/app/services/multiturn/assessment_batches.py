"""固定会话报告分段与评审轮次，不让分段改变检查项和原文覆盖。"""
import json

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgePacket, build_judge_messages


def validate_batches(packet: JudgePacket, batches: tuple[JudgePacket, ...], input_budget: int) -> None:
    """检查每段预算、原文一致及检查项恰好分配一次，拒绝遗漏来源。"""
    packet.validate_sources()
    if not batches:
        raise ValueError("分段输入无效")
    if packet.scope == "dialogue":
        if batches != _dialogue_batches(packet, input_budget):
            raise ValueError("单轮分段与固定原文及预算推导的规范材料不一致")
        return
    if packet.rag is not None:
        raise ValueError("会话报告分段不能包含单轮 RAG 材料")
    checks = {item.id: item for item in packet.checks}
    sources = {item.id: item for item in packet.sources}
    seen_checks: set[str] = set()
    seen_sources: set[str] = set()
    for batch in batches:
        batch.validate_sources()
        if (batch.scope != packet.scope or batch.through_turn != packet.through_turn
                or (packet.scope == "session" and batch.answer != packet.answer) or batch.rag is not None):
            raise ValueError("分段范围必须与固定评审一致")
        if estimate_tokens(build_judge_messages(batch)) > input_budget:
            raise ValueError("报告分段超过评审输入预算")
        for check in batch.checks:
            if check.id in seen_checks or checks.get(check.id) != check:
                raise ValueError("报告检查项被重复或修改")
            seen_checks.add(check.id)
        for source in batch.sources:
            if sources.get(source.id) != source:
                raise ValueError("报告分段原文被修改")
            seen_sources.add(source.id)
    if packet.scope == "session" and (seen_checks != set(checks) or seen_sources != set(sources)):
        raise ValueError("报告分段遗漏固定检查项或原文")
    if packet.scope == "dialogue" and seen_checks != set(checks):
        raise ValueError("单轮分段重复或遗漏检查项")


def _dialogue_batches(packet: JudgePacket, input_budget: int) -> tuple[JudgePacket, ...]:
    """从不可变父快照确定每项完整材料，预算不足仅允许明确未知的派生包。"""
    packet.validate_sources()
    if packet.scope != "dialogue" or type(input_budget) is not int or input_budget <= 0:
        raise ValueError("单轮分段范围或预算无效")
    batches: list[JudgePacket] = []
    for check in packet.checks:
        selected = packet.sources
        if check.id == "expression":
            selected = tuple(source for source in packet.sources if source.turn == packet.through_turn)
        elif check.requirement_id is not None and check.ambiguous_source_id is None and check.required_source_ids:
            anchors = [source.turn for source in packet.sources if source.id in check.required_source_ids]
            if anchors:
                selected = tuple(source for source in packet.sources if source.turn >= min(anchors))
        # RAG 证据只在表达包独立评一次；其他质量项不重复计资料分或重复发送资料。
        rag = packet.rag if check.id == "expression" else None
        candidate = packet.model_copy(update={"checks": (check,), "sources": selected, "rag": rag})
        if estimate_tokens(build_judge_messages(candidate)) <= input_budget:
            batches.append(candidate)
            continue
        # 资料包过大仍可核验表达；证据未核验由父快照与子包差异显式汇总为未知。
        if rag is not None:
            candidate = candidate.model_copy(update={"rag": None})
            if estimate_tokens(build_judge_messages(candidate)) <= input_budget:
                batches.append(candidate)
                continue
        unknown = check.model_copy(update={"required_applicability": "unknown", "allowed_source_ids": (),
            "required_source_ids": (), "description": check.description + "；完整必要材料超过预算，本项无法核验。"})
        minimal = candidate.model_copy(update={"checks": (unknown,), "sources": (), "rag": None})
        if estimate_tokens(build_judge_messages(minimal)) > input_budget:
            minimal = minimal.model_copy(update={"answer": "[原回答保留在父快照；本项完整材料超预算，无法核验]"})
        if estimate_tokens(build_judge_messages(minimal)) > input_budget:
            raise ValueError("检查项说明及固定提示词超过预算，请增加评审输入预算")
        batches.append(minimal)
    return tuple(batches)


def build_dialogue_batches(packet: JudgePacket, input_budget: int) -> tuple[JudgePacket, ...]:
    """构造普通/RAG 单轮的固定逐项包；原始回答与全部资料始终留在父快照。"""
    return _dialogue_batches(packet, input_budget)


def snapshot_batches(snapshot: dict[str, object]) -> tuple[JudgePacket, ...]:
    """读取旧单段或新固定分段，供认领后的执行使用。"""
    packet = JudgePacket.model_validate_json(json.dumps(snapshot["packet"]))
    if "batches" not in snapshot:
        return (packet,)
    batches = tuple(JudgePacket.model_validate_json(json.dumps(item)) for item in snapshot["batches"])
    validate_batches(packet, batches, snapshot["inputBudget"])
    return batches


def batch_count(snapshot: dict[str, object]) -> int:
    """使用已验证的固定快照确定每次完整评审的调用数量。"""
    return len(snapshot["batches"]) if "batches" in snapshot else 1
