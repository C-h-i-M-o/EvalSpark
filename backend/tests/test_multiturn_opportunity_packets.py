"""具体机会评审必须使用完整区间和双边证据，不能只读有利引文。"""
import json

import pytest

from app.services.multiturn.judge import JudgeSource, _parse
from app.services.multiturn.judge import JudgeCheck, JudgePacket
from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import build_judge_messages
from app.services.multiturn.opportunities import extract_opportunities
from app.services.multiturn.opportunity_packets import build_opportunity_packets
from test_multiturn_judge import StubJudge
from test_multiturn_opportunities import result
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_opportunity_packet_keeps_intervening_changes_and_requires_both_sources() -> None:
    """中途预算修改必须进入评审，适用检查只引用回答一侧则拒绝。"""
    sources = (JudgeSource(id="turn:1:user", turn=1, text="我的预算是5000元"),
        JudgeSource(id="turn:2:user", turn=2, text="预算改为6000元"),
        JudgeSource(id="response:9", turn=4, text="继续按5000元预算安排"))
    discovery = await extract_opportunities(StubJudge(json.dumps(result())), sources, through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=AsyncMock())
    plan = build_opportunity_packets(sources, discovery, through_turn=4, input_budget=10000)
    packet = plan.batches[0]
    assert packet.sources == sources
    assert packet.checks[0].required_source_ids == ("turn:1:user", "response:9")
    item = {"id": packet.checks[0].id, "applicability": "applicable", "rating": 0,
        "passed": None, "reason": "没有采用后续修改", "evidence": [{"source_id": "response:9", "quote": "5000元"}]}
    with pytest.raises(ValueError, match="必要来源"):
        _parse(json.dumps({"items": [item]}), packet)
    item["evidence"].append({"source_id": "turn:1:user", "quote": "5000元"})
    assert _parse(json.dumps({"items": [item]}), packet)[0].rating == 0
    unknown = build_opportunity_packets(sources, discovery, through_turn=4,
        input_budget=estimate_tokens(build_judge_messages(packet)) - 1)
    assert unknown.batches[0].checks[0].required_applicability == "unknown"
    assert unknown.limitations


@pytest.mark.asyncio
async def test_grounded_opportunity_cannot_be_replayed_against_changed_sources() -> None:
    """持久化后装配重新核验原文及轮次，不能把旧机会移植到新快照。"""
    sources = (JudgeSource(id="turn:1:user", turn=1, text="我的预算是5000元"),
        JudgeSource(id="response:9", turn=4, text="继续按5000元预算安排"))
    discovery = await extract_opportunities(StubJudge(json.dumps(result())), sources, through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=AsyncMock())
    for changed in (sources[0].model_copy(update={"text": "修改后的原文"}),
                    sources[0].model_copy(update={"turn": 2})):
        with pytest.raises(ValueError, match="快照"):
            build_opportunity_packets((changed, sources[1]), discovery, through_turn=4, input_budget=10000)


def test_required_sources_must_be_unique_and_within_allowed_scope() -> None:
    """必要来源不能绕过允许范围，unknown 也不能迫使模型伪造引用。"""
    source = JudgeSource(id="response:1", turn=1, text="回答")
    for required in (("foreign",), (source.id, source.id)):
        packet = JudgePacket(scope="session", through_turn=1, answer="范围说明", sources=(source,),
            checks=(JudgeCheck(id="check", dimension="goal", description="检查", required_source_ids=required),))
        with pytest.raises(ValueError, match="必要来源"):
            packet.validate_sources()
    packet = packet.model_copy(update={"checks": (JudgeCheck(id="check", dimension="goal", description="检查",
        required_source_ids=(source.id,)),)})
    packet.validate_sources()
    item = {"id": "check", "applicability": "unknown", "rating": None, "passed": None,
            "reason": "依据不足", "evidence": []}
    assert _parse(json.dumps({"items": [item]}), packet)[0].applicability == "unknown"
