"""多轮评分的确定性计算；不在本模块调用模型。"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from app.schemas.multiturn import Applicability, CheckItem, RAGAssertion, Reassessment, SessionOpportunity

ZERO = Decimal("0")
TEN = Decimal("10")
CHAT_WEIGHTS = {"solution": Decimal(".40"), "context": Decimal(".25"), "instruction": Decimal(".20"), "expression": Decimal(".15")}
SESSION_WEIGHTS = {"goal": Decimal(".30"), "memory": Decimal(".25"), "consistency": Decimal(".20"), "correction": Decimal(".15"), "efficiency": Decimal(".10")}
RAG_WEIGHTS = {"faithfulness": Decimal(".50"), "citation_correctness": Decimal(".30"), "citation_completeness": Decimal(".20")}


@dataclass
class ScoreResult:
    """分数同时携带覆盖率，避免隐藏未知状态。"""
    dimensions: dict[str, Decimal | None]
    final: Decimal | None
    coverage: Decimal
    critical_passed: bool | None = True
    applicability: dict[str, Applicability] = field(default_factory=dict)


@dataclass
class RagEvidenceResult:
    """资料支持和实际引用支持分别计算。"""
    faithfulness: Decimal | None
    citation_correctness: Decimal | None
    citation_completeness: Decimal | None
    final: Decimal | None
    applicability: dict[str, Applicability] = field(default_factory=dict)
    coverage: Decimal = ZERO


@dataclass
class ReassessmentResult(ScoreResult):
    """正式复评返回稳定性状态和有效次数。"""
    status: str = "judge_failed"
    valid_runs: int = 0
    ranges: dict[str, Decimal] = field(default_factory=dict)


def _round(value: Decimal) -> Decimal:
    """输出时统一使用十进制四舍五入。"""
    return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def _combine(dimensions: dict[str, Decimal | None], states: dict[str, Applicability],
             weights: dict[str, Decimal], critical: bool | None = True) -> ScoreResult:
    """只排除真正不适用的维度，未知维度保留在覆盖率分母。"""
    applicable = sum((w for k, w in weights.items() if states[k] != "not_applicable"), ZERO)
    known = sum((w for k, w in weights.items() if states[k] == "applicable"), ZERO)
    coverage = _round(known / applicable) if applicable else ZERO
    final = None
    if applicable and "unknown" not in states.values():
        final = _round(sum(((dimensions[k] or ZERO) * w for k, w in weights.items()), ZERO) / applicable)
    return ScoreResult({k: _round(v) if v is not None else None for k, v in dimensions.items()}, final, coverage, critical, states)


def _weighted(items: list[CheckItem] | list[SessionOpportunity], weights: dict[str, Decimal]) -> ScoreResult:
    """校验检查项，缺项及同维度未知项都不能默认为通过。"""
    if len({item.id for item in items}) != len(items):
        raise ValueError("检查项 id 不能重复")
    critical: bool | None = True
    for item in items:
        if not item.id.strip() or item.dimension not in weights:
            raise ValueError("检查项标识或维度无效")
        if item.applicability == "applicable":
            if item.rating is None or not item.evidence or any(not v.strip() for v in item.evidence):
                raise ValueError("适用检查项必须提供分数和原文证据")
        elif item.rating is not None:
            raise ValueError("非适用或未知检查项不能携带分数")
        if isinstance(item, CheckItem) and item.critical:
            if item.applicability == "applicable" and item.passed is None:
                raise ValueError("关键要求必须给出通过状态")
            if item.applicability == "applicable" and item.passed is False:
                critical = False
            elif item.applicability == "unknown" and critical is not False:
                critical = None
    dimensions: dict[str, Decimal | None] = {}
    states: dict[str, Applicability] = {}
    for name in weights:
        selected = [item for item in items if item.dimension == name]
        ratings = [item.rating for item in selected if item.applicability == "applicable" and item.rating is not None]
        if not selected or any(item.applicability == "unknown" for item in selected):
            states[name], dimensions[name] = "unknown", None
        elif ratings:
            states[name] = "applicable"
            dimensions[name] = TEN * Decimal(sum(ratings)) / (Decimal(4) * len(ratings))
        else:
            states[name], dimensions[name] = "not_applicable", None
    return _combine(dimensions, states, weights, critical)


def score_dialogue(items: list[CheckItem]) -> ScoreResult:
    """计算当前问题、上下文、要求和表达四个维度。"""
    return _weighted(items, CHAT_WEIGHTS)


def score_session(items: list[SessionOpportunity] | list[CheckItem]) -> ScoreResult:
    """按验证机会计算会话表现，不以单轮平均替代。"""
    return _weighted(items, SESSION_WEIGHTS)


def _support_dimension(values: list[Decimal | None]) -> tuple[Decimal | None, Applicability]:
    """无评价对象为不适用，有对象但缺少判断为未知。"""
    if not values:
        return None, "not_applicable"
    if any(value is None for value in values):
        return None, "unknown"
    return TEN * sum((value for value in values if value is not None), ZERO) / len(values), "applicable"


def score_rag_evidence(assertions: list[RAGAssertion], answer: str, evidence_ids: set[str]) -> RagEvidenceResult:
    """逐断言验证来源，独立计算事实支持、引用关系和引用覆盖。"""
    if len({a.id for a in assertions}) != len(assertions):
        raise ValueError("断言 id 不能重复")
    seen: set[str] = set()
    relations: list[Decimal | None] = []
    completeness: list[Decimal | None] = []
    for item in assertions:
        if not item.answer_text.strip() or item.answer_text not in answer:
            raise ValueError("断言原文必须实际出现在候选回答中")
        if item.answer_text.strip() in seen:
            raise ValueError("相同断言不能重复计分")
        seen.add(item.answer_text.strip())
        if len(set(item.cited_evidence_ids)) != len(item.cited_evidence_ids):
            raise ValueError("同一断言引用不能重复")
        if len(set(item.invalid_cited_evidence_ids)) != len(item.invalid_cited_evidence_ids):
            raise ValueError("同一断言错误引用不能重复")
        for evidence_id in item.invalid_cited_evidence_ids:
            if not evidence_id.strip() or evidence_id in evidence_ids or evidence_id in item.cited_evidence_ids or f"[{evidence_id}]" not in answer:
                raise ValueError("错误引用必须实际出现且不属于已知证据")
        for evidence_id in item.cited_evidence_ids:
            if evidence_id not in evidence_ids or f"[{evidence_id}]" not in answer:
                raise ValueError("引用必须属于已知证据且实际出现在回答中")
        if item.citation_support is not None and not item.cited_evidence_ids:
            raise ValueError("没有引用时不能提供引用支持分数")
        if item.citation_supports and set(item.citation_supports) != set(item.cited_evidence_ids):
            raise ValueError("逐引用支持值必须与实际引用集合一致")
        if len(item.cited_evidence_ids) > 1 and not item.citation_supports:
            raise ValueError("多个引用必须分别提供支持判断")
        values = list(item.citation_supports.values()) if item.citation_supports else ([item.citation_support] if item.cited_evidence_ids else [])
        if item.support is not None and any(v is not None and v > item.support for v in [*values, item.citation_support]):
            raise ValueError("引用支持程度不能高于资料整体支持程度")
        relations.extend(values)
        # 假引用属于回答质量问题，每条实际关系计零；不能使整次评审失效。
        relations.extend(ZERO for _ in item.invalid_cited_evidence_ids)
        if item.needs_citation:
            completeness.append(item.citation_support if values else ZERO)
    dimensions: dict[str, Decimal | None] = {}
    states: dict[str, Applicability] = {}
    for name, values in (("faithfulness", [a.support for a in assertions if a.needs_citation]),
                         ("citation_correctness", relations), ("citation_completeness", completeness)):
        dimensions[name], states[name] = _support_dimension(values)
    result = _combine(dimensions, states, RAG_WEIGHTS)
    return RagEvidenceResult(result.dimensions["faithfulness"], result.dimensions["citation_correctness"],
        result.dimensions["citation_completeness"], result.final, result.applicability, result.coverage)


def aggregate_reassessments(runs: list[Reassessment], *, session: bool = False) -> ReassessmentResult:
    """按每个十分制维度判断正式复评分歧，一致失败仍然是稳定判断。"""
    if len(runs) != 3 or {run.run_index for run in runs} != {1, 2, 3}:
        raise ValueError("正式复评必须恰好包含三次不同评审")
    valid = [run for run in runs if run.valid]
    if len(valid) < 2:
        return ReassessmentResult({}, None, ZERO, None, status="judge_failed", valid_runs=len(valid))
    weights = SESSION_WEIGHTS if session else CHAT_WEIGHTS
    results = [_weighted(run.items, weights) for run in valid]
    baseline = {item.id: item for item in valid[0].items}
    for run in valid[1:]:
        if {item.id for item in run.items} != set(baseline):
            return ReassessmentResult({}, None, ZERO, None, status="judge_unstable", valid_runs=len(valid))
        for item in run.items:
            base = baseline[item.id]
            if (item.dimension, item.requirement_id, item.applicability, item.critical, item.passed if item.critical else None) != (base.dimension, base.requirement_id, base.applicability, base.critical, base.passed if base.critical else None):
                return ReassessmentResult({}, None, ZERO, None, status="judge_unstable", valid_runs=len(valid))
    dimensions: dict[str, Decimal | None] = {}
    ranges: dict[str, Decimal] = {}
    for name in weights:
        scores = [r.dimensions[name] for r in results if r.dimensions[name] is not None]
        dimensions[name] = sum(scores, ZERO) / len(scores) if scores else None
        if scores:
            ranges[name] = max(scores) - min(scores)
    result = _combine(dimensions, results[0].applicability, weights, results[0].critical_passed)
    status = "judge_unstable" if any(v > 2 for v in ranges.values()) else ("incomplete" if "unknown" in result.applicability.values() else "scored")
    return ReassessmentResult(result.dimensions, result.final if status == "scored" else None,
        result.coverage, result.critical_passed, result.applicability, status, len(valid), ranges)


def aggregate_rag_assertion_runs(runs: list[list[RAGAssertion] | None], answer: str,
                                evidence_ids: set[str]) -> ReassessmentResult:
    """先核验逐片段适用性一致，再聚合证据分，避免均分掩盖事实判断分歧。"""
    results = [score_rag_evidence(run, answer, evidence_ids) if run is not None else None for run in runs]
    result = aggregate_rag_reassessments(results)
    valid = [run for run in runs if run is not None]
    if len(valid) >= 2:
        baseline = {item.id: (item.answer_text, item.needs_citation, tuple(item.cited_evidence_ids),
                              tuple(item.invalid_cited_evidence_ids)) for item in valid[0]}
        for run in valid[1:]:
            current = {item.id: (item.answer_text, item.needs_citation, tuple(item.cited_evidence_ids),
                                 tuple(item.invalid_cited_evidence_ids)) for item in run}
            if baseline != current:
                result.status, result.final = "judge_unstable", None
                break
    return result


def aggregate_rag_reassessments(runs: list[RagEvidenceResult | None]) -> ReassessmentResult:
    """聚合三次 RAG 证据复评，并按维度范围判定结果稳定性。"""
    if len(runs) != 3:
        raise ValueError("RAG 正式复评必须恰好包含三次评审")
    valid = [run for run in runs if run is not None]
    if len(valid) < 2:
        return ReassessmentResult({}, None, ZERO, None, status="judge_failed", valid_runs=len(valid))

    applicability = valid[0].applicability
    for run in valid[1:]:
        if any(run.applicability.get(name) != applicability.get(name) for name in RAG_WEIGHTS):
            return ReassessmentResult({}, None, ZERO, None, status="judge_unstable", valid_runs=len(valid))

    dimensions: dict[str, Decimal | None] = {}
    ranges: dict[str, Decimal] = {}
    for name in RAG_WEIGHTS:
        scores = [getattr(run, name) for run in valid if getattr(run, name) is not None]
        dimensions[name] = sum(scores, ZERO) / len(scores) if scores else None
        if scores:
            ranges[name] = max(scores) - min(scores)

    combined = _combine(dimensions, applicability, RAG_WEIGHTS, critical=None)
    status = "judge_unstable" if any(value > Decimal("2") for value in ranges.values()) else (
        "incomplete" if "unknown" in applicability.values() else "scored"
    )
    return ReassessmentResult(
        combined.dimensions,
        combined.final if status == "scored" else None,
        combined.coverage,
        None,
        combined.applicability,
        status,
        len(valid),
        ranges,
    )
