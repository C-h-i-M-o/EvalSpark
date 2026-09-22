"""语义报告不把覆盖审核计入成功机会，并保持长报告未知约束。"""
import json
from unittest.mock import AsyncMock

import pytest

from app.services.multiturn.judge import JudgeSource, _parse
from app.services.multiturn.opportunities import OpportunityResult, extract_opportunities
from app.services.multiturn.report_plan import build_report_plan
from app.services.multiturn.semantic_report import build_semantic_plan
from test_multiturn_judge import StubJudge
from test_multiturn_opportunities import result, sources


def test_version_two_adds_cross_boundary_opportunity_without_local_duplicates() -> None:
    """新版本发现边界关系，单窗重复不计分，旧版本仍只有原窗口。"""
    from test_multiturn_report_bridges import windows
    from app.schemas.multiturn import EvidenceReference
    from app.services.multiturn.opportunities import Opportunity
    from app.services.multiturn.semantic_report import discovery_inputs
    batches = windows()
    original = batches[0].model_copy(update={"sources": (*batches[0].sources, *batches[1].sources),
        "checks": tuple(check for batch in batches for check in batch.checks)})
    crossing = Opportunity("opportunity:cross", "memory", "根据最新预算回答",
        EvidenceReference(source_id="user:1", turn=1, quote="预算100"),
        EvidenceReference(source_id="response:4", turn=4, quote="最终采用200"), ())
    local = Opportunity("opportunity:local", "goal", "遵守修改",
        EvidenceReference(source_id="user:3", turn=3, quote="改为200"), crossing.target, ())
    results = (OpportunityResult("ready"), OpportunityResult("ready"), OpportunityResult("ready", (crossing, local)))
    plan = build_semantic_plan(original, batches, results, 10000, version=2)
    checks = {check.id: check for check in plan.packet.checks}
    assert "opportunity:cross" in checks and "opportunity:local" not in checks
    selected = next(batch for batch in plan.batches if batch.checks[0].id == crossing.id)
    assert selected.sources == original.sources
    audit = next(batch for batch in plan.batches if batch.checks[0].id == "discovery:3:goal")
    assert '"left": ["user:1", "response:2"]' in audit.checks[0].description
    assert len(discovery_inputs(batches, 10000, 1)) == 2
    assert len(discovery_inputs(batches, 10000, 2)) == 3
    with pytest.raises(ValueError, match="提取次数"):
        build_semantic_plan(original, batches, results[:2], 10000, version=2)


@pytest.mark.asyncio
async def test_semantic_report_replaces_window_scores_and_rejects_coverage_ratings() -> None:
    """具体机会只计一次，覆盖审核不能输出一个额外高分抵消失败。"""
    original = build_report_plan(sources(), 4, 10000)
    discovery = await extract_opportunities(StubJudge(json.dumps(result())), sources(), through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=AsyncMock())
    plan = build_semantic_plan(original.packet, original.batches, (discovery,), 10000)
    assert not any(check.id.startswith("window:") for check in plan.packet.checks)
    assert sum(check.id.startswith("opportunity:") for check in plan.packet.checks) == 1
    audit = plan.batches[0]
    assert len(audit.checks) == 5 and all(check.coverage_only for check in audit.checks)
    items = [{"id": check.id, "applicability": "applicable", "rating": 4, "passed": None,
              "reason": "无遗漏", "evidence": [{"source_id": sources()[0].id, "quote": sources()[0].text}]}
             for check in audit.checks]
    with pytest.raises(ValueError, match="覆盖审核"):
        _parse(json.dumps({"items": items}), audit)


@pytest.mark.parametrize("version", [1, 2])
def test_incomplete_windows_preserve_global_unknown_and_full_sources(version: int) -> None:
    """原文逐窗读取不等于全局关系完整，不完整提取不能清除全局未知项。"""
    material = tuple(JudgeSource(id=f"response:{index}", turn=index, text="事实材料" * 240) for index in range(1, 8))
    original = build_report_plan(material, 7, 7000)
    from app.services.multiturn.semantic_report import discovery_inputs
    discoveries = tuple(OpportunityResult("incomplete", unresolved=("缺少关系依据",))
                        for _ in discovery_inputs(original.batches, 7000, version))
    plan = build_semantic_plan(original.packet, original.batches, discoveries, 7000, version=version)
    assert {source.id for batch in plan.batches for source in batch.sources} == {source.id for source in material}
    guards = [check for check in plan.packet.checks if check.id.startswith("cross_window:")]
    assert len(guards) == 3 and all(check.required_applicability == "unknown" for check in guards)
    assert all(check.required_applicability == "unknown" for check in plan.packet.checks if check.id.startswith("discovery:"))
