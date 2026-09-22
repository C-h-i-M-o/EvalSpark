"""将窗口提取结果装配为具体机会和不计分的覆盖审核。"""
import json
from dataclasses import replace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.adapters.base import ModelClient

from app.schemas.multiturn import EvidenceReference
from app.services.multiturn.assessment_batches import validate_batches
from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgePacket, build_judge_messages
from app.services.multiturn.opportunities import Opportunity, OpportunityResult
from app.services.multiturn.opportunity_packets import build_opportunity_packets
from app.services.multiturn.report_plan import ReportPlan
from app.services.multiturn.report_bridges import build_boundary_plan
from app.services.multiturn.report_relations import build_relation_plan, consolidate_discoveries
from app.services.multiturn.scoring import SESSION_WEIGHTS


def read_discovery(value: dict[str, object]) -> OpportunityResult:
    """还原已校验的持久化提取结果，引用结构仍通过严格模型校验。"""
    return OpportunityResult(value["status"], tuple(Opportunity(item["id"], item["dimension"], item["description"],
        EvidenceReference.model_validate(item["trigger"]), EvidenceReference.model_validate(item["target"]),
        tuple(EvidenceReference.model_validate(ref) for ref in item["supporting"]))
        for item in value["opportunities"]), tuple(value["unresolved"]), value["errorCode"])


def discovery_windows(batches: tuple[JudgePacket, ...]) -> tuple[JudgePacket, ...]:
    """只提取原报告窗口，不对重复出现的要求专用材料再次收费。"""
    return tuple(batch for batch in batches if any(check.id.startswith("window:") for check in batch.checks))


def discovery_inputs(batches: tuple[JudgePacket, ...], budget: int, version: int) -> tuple[JudgePacket, ...]:
    """版本二追加确定性的边界材料，旧报告保持原收费次数和来源。"""
    if version not in (1, 2, 3):
        raise ValueError("不支持的报告准备版本")
    windows = discovery_windows(batches)
    if version == 1 or not windows:
        return windows
    bridges = build_boundary_plan(windows, budget)
    distant = build_relation_plan(windows, budget).materials if version == 3 else ()
    return (*windows, *(windows[0].model_copy(update={"sources": material.sources})
        for material in (*bridges.materials, *distant)))


def scoped_discoveries(batches: tuple[JudgePacket, ...], discoveries: tuple[OpportunityResult, ...],
                       budget: int, version: int) -> tuple[OpportunityResult, ...]:
    """边界提取只增加左触发右目标的机会，原始流水仍保留全部提取结果。"""
    inputs = discovery_inputs(batches, budget, version)
    if len(inputs) != len(discoveries):
        raise ValueError("提取次数与固定窗口及边界不一致")
    for packet, discovery in zip(inputs, discoveries):
        ids = {source.id for source in packet.sources}
        if any(reference.source_id not in ids for item in discovery.opportunities
               for reference in (item.trigger, item.target, *item.supporting)):
            raise ValueError("提取机会引用超出本次原文范围")
    if version == 1:
        return discoveries
    windows = discovery_windows(batches)
    bridges = build_boundary_plan(windows, budget)
    distant = build_relation_plan(windows, budget).materials if version == 3 else ()
    return (*discoveries[:len(windows)], *(replace(discovery, opportunities=tuple(item
        for item in discovery.opportunities if item.trigger.source_id in material.left_ids
        and item.target.source_id in material.right_ids))
        for material, discovery in zip((*bridges.materials, *distant), discoveries[len(windows):])))


def build_semantic_plan(original: JudgePacket, batches: tuple[JudgePacket, ...],
                        discoveries: tuple[OpportunityResult, ...], budget: int, *, version: int = 1) -> ReportPlan:
    """用具体机会替换通用窗口分数，保留原有要求和跨窗口未知约束。"""
    original_windows = discovery_windows(batches)
    windows = discovery_inputs(batches, budget, version)
    discoveries = scoped_discoveries(batches, discoveries, budget, version)
    if not windows or len(windows) != len(discoveries) or any(item.status == "failed" for item in discoveries):
        raise ValueError("窗口提取尚未完整成功")
    result: list[JudgePacket] = []
    limitations: list[str] = []
    bridges = build_boundary_plan(original_windows, budget) if version >= 2 else None
    relations = build_relation_plan(original_windows, budget) if version == 3 else None
    materials = (*(bridges.materials if bridges else ()), *(relations.materials if relations else ()))
    if version >= 2:
        limitations.extend(f"边界 {number} 的最小完整原文包超过预算，跨界机会尚未检查。"
            for number in bridges.uncovered)
    if version == 3:
        limitations.extend(relations.limitations)
    seen: dict[str, JudgePacket] = {}
    for batch in batches:
        checks = tuple(check for check in batch.checks if not check.id.startswith("window:"))
        if checks:
            result.append(batch.model_copy(update={"checks": checks}))
    for index, (window, discovery) in enumerate(zip(windows, discoveries), 1):
        boundary_description = ""
        if index > len(original_windows):
            material = materials[index - len(original_windows) - 1]
            boundary_description = ("仅检查触发在左侧来源、目标在右侧来源的跨界机会；单窗机会不在本次覆盖范围。"
                + json.dumps({"left": material.left_ids, "right": material.right_ids}, ensure_ascii=False))
        plan = build_opportunity_packets(original.sources,
            OpportunityResult(discovery.status, (), discovery.unresolved) if version == 3 else discovery,
            through_turn=original.through_turn, input_budget=budget)
        limitations.extend(plan.limitations)
        for batch in plan.batches:
            batch = batch.model_copy(update={"answer": original.answer})
            key = batch.checks[0].id
            if key in seen and seen[key] != batch:
                raise ValueError("重复机会的定义不一致")
            seen[key] = batch
        checks = []
        for dimension in SESSION_WEIGHTS:
            accepted = [{"description": item.description, "trigger": item.trigger.model_dump(),
                         "target": item.target.model_dump()} for item in discovery.opportunities if item.dimension == dimension]
            checks.append(JudgeCheck(id=f"discovery:{index}:{dimension}", dimension=dimension, coverage_only=True,
                required_applicability="unknown" if discovery.status != "ready" else None,
                description=boundary_description + "检查本窗口此维度的机会是否全部列入以下清单；发现遗漏或证据不足用unknown，"
                    "确认无遗漏用not_applicable。清单：" + json.dumps(accepted, ensure_ascii=False)))
        audit = window.model_copy(update={"checks": tuple(checks)})
        if estimate_tokens(build_judge_messages(audit)) > budget:
            audit = window.model_copy(update={"checks": tuple(JudgeCheck(id=check.id, dimension=check.dimension,
                description="机会清单无法完整装入预算，覆盖率未知。", required_applicability="unknown") for check in checks)})
            limitations.append(f"窗口 {index} 的机会覆盖审核超出预算，保留未知。")
        result.append(audit)
    if version == 3:
        plan = build_opportunity_packets(original.sources, consolidate_discoveries(discoveries),
            through_turn=original.through_turn, input_budget=budget)
        limitations.extend(plan.limitations)
        seen = {batch.checks[0].id: batch.model_copy(update={"answer": original.answer}) for batch in plan.batches}
    result.extend(seen.values())
    parent = original.model_copy(update={"checks": tuple(check for batch in result for check in batch.checks)})
    validate_batches(parent, tuple(result), budget)
    return ReportPlan(parent, tuple(result), tuple(dict.fromkeys(limitations)))


async def prepare_report(sessions: async_sessionmaker[AsyncSession], job_id: int, owner: int,
                         client: ModelClient, snapshot: dict[str, object],
                         output_tokens: int) -> dict[str, object]:
    """逐窗口准备并原子固定正式分段，返回执行器后续三组共用的快照。"""
    from app.models.conversation import ConversationAssessment
    from app.services.multiturn.assessment_batches import snapshot_batches
    from app.services.multiturn.preparation import PreparationStore, execute_preparation
    original = JudgePacket.model_validate_json(json.dumps(snapshot["packet"]))
    batches = snapshot_batches(snapshot)
    version = snapshot["report"].get("semanticPreparation", 1)
    discoveries: list[OpportunityResult] = []
    for index, window in enumerate(discovery_inputs(batches, snapshot["inputBudget"], version), 1):
        discoveries.append(await execute_preparation(sessions, job_id, owner, index, window.sources, client,
            through_turn=original.through_turn, input_budget=snapshot["inputBudget"], output_tokens=output_tokens))
        if discoveries[-1].status == "failed":
            raise ValueError("报告机会提取失败，已保存实际用量")
    plan = build_semantic_plan(original, batches, tuple(discoveries), snapshot["inputBudget"], version=version)
    async with sessions() as db:
        await PreparationStore().freeze(db, job_id, owner, plan.packet, plan.batches)
        return (await db.get(ConversationAssessment, job_id, populate_existing=True)).input_json
