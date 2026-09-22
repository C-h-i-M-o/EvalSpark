"""语义机会只来自固定原文，不能由提取模型自报可信轮次或分数。"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services.multiturn.judge import JudgeSource
from app.services.multiturn.opportunities import extract_opportunities
from test_multiturn_judge import StubJudge


def sources() -> tuple[JudgeSource, ...]:
    """同一预算在早期用户输入和后续回答出现，用于验证跨轮记忆机会。"""
    return (JudgeSource(id="turn:1:user", turn=1, text="我的预算是5000元"),
            JudgeSource(id="response:9", turn=4, text="继续按5000元预算安排"))


def result() -> dict[str, object]:
    """提取结果只声明机会及引文，不声明成功分数或轮次。"""
    return {"complete": True, "unresolved": [], "opportunities": [{"dimension": "memory", "description": "后续安排保持初始预算",
        "trigger": {"source_id": "turn:1:user", "quote": "预算是5000元"},
        "target": {"source_id": "response:9", "quote": "按5000元预算安排"}, "supporting": []}]}


@pytest.mark.asyncio
async def test_grounded_opportunity_gets_server_turns_and_stable_identity() -> None:
    """轮次由原文查表，同一机会在重复提取时保持标识。"""
    before, after = AsyncMock(), AsyncMock()
    first = await extract_opportunities(StubJudge(json.dumps(result())), sources(), through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=before, after_call=after)
    second = await extract_opportunities(StubJudge(json.dumps(result())), sources(), through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=AsyncMock())
    assert first.status == "ready"
    assert first.opportunities[0].trigger.turn == 1 and first.opportunities[0].target.turn == 4
    assert first.opportunities[0].id == second.opportunities[0].id
    before.assert_awaited_once()
    assert after.await_args.args[0].usage.total_tokens == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["quote", "target", "duplicate", "rating", "turn", "future", "dimension"])
async def test_invalid_opportunities_keep_usage_but_never_enter_scoring(mutation: str) -> None:
    """伪造引文、角色、重复机会、自报轮次和评分均失败但保留本次用量。"""
    payload = result()
    item = payload["opportunities"][0]
    if mutation == "quote":
        item["trigger"]["quote"] = "预算无限"
    elif mutation == "target":
        item["target"] = item["trigger"]
    elif mutation == "duplicate":
        payload["opportunities"].append(item)
    elif mutation == "rating":
        item["rating"] = 4
    elif mutation == "turn":
        item["target"]["turn"] = 999
    elif mutation == "future":
        item["trigger"], item["target"] = item["target"], item["trigger"]
    else:
        item["dimension"] = "popularity"
    after = AsyncMock()
    output = await extract_opportunities(StubJudge(json.dumps(payload)), sources(), through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=after)
    assert output.status == "failed" and output.opportunities == ()
    assert after.await_args.args[0].usage.total_tokens == 20


@pytest.mark.asyncio
async def test_budget_future_and_incomplete_discovery_never_claim_full_coverage() -> None:
    """输入预算及未来材料在登记调用前检查，提取不完整保持独立状态。"""
    before = AsyncMock()
    client = StubJudge(json.dumps(result()))
    for budget, through in ((1, 4), (10000, 2)):
        with pytest.raises(ValueError):
            await extract_opportunities(client, sources(), through_turn=through, input_budget=budget,
                output_tokens=2048, before_call=before, after_call=AsyncMock())
    before.assert_not_awaited()
    assert not client.requests
    payload = result()
    payload["complete"], payload["unresolved"] = False, ["其他目标缺少足够原文"]
    output = await extract_opportunities(StubJudge(json.dumps(payload)), sources(), through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=AsyncMock())
    assert output.status == "incomplete" and len(output.opportunities) == 1


@pytest.mark.asyncio
async def test_cancelled_call_records_unknown_once_without_retry() -> None:
    """提取被取消仍结束用量回调，不能自动重试收费调用。"""
    client = StubJudge("{}")
    client.chat = AsyncMock(side_effect=asyncio.CancelledError)
    before, after = AsyncMock(), AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await extract_opportunities(client, sources(), through_turn=4, input_budget=10000,
            output_tokens=2048, before_call=before, after_call=after)
    before.assert_awaited_once()
    after.assert_awaited_once_with(None)
    client.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_supplier_and_accounting_failures_are_distinct() -> None:
    """上游失败记录未知用量，结算失败则抛出，禁止伪装成已结算结果。"""
    client = StubJudge("{}")
    client.chat = AsyncMock(side_effect=RuntimeError("上游不可用"))
    after = AsyncMock()
    output = await extract_opportunities(client, sources(), through_turn=4, input_budget=10000,
        output_tokens=2048, before_call=AsyncMock(), after_call=after)
    assert output.error_code == "opportunity_call_failed"
    after.assert_awaited_once_with(None)
    with pytest.raises(RuntimeError, match="结算失败"):
        await extract_opportunities(StubJudge(json.dumps(result())), sources(), through_turn=4,
            input_budget=10000, output_tokens=2048, before_call=AsyncMock(),
            after_call=AsyncMock(side_effect=RuntimeError("结算失败")))


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension,expected", [("memory", "failed"), ("consistency", "failed"),
    ("correction", "ready"), ("goal", "ready"), ("efficiency", "ready")])
async def test_same_turn_checks_respect_dimension_semantics(dimension: str, expected: str) -> None:
    """同轮纠正可以检查，但不能冒充跨轮记忆或一致性。"""
    payload = result()
    payload["opportunities"][0]["dimension"] = dimension
    current = (sources()[0].model_copy(update={"turn": 4}), sources()[1])
    output = await extract_opportunities(StubJudge(json.dumps(payload)), current, through_turn=4,
        input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=AsyncMock())
    assert output.status == expected


@pytest.mark.asyncio
async def test_future_support_and_duplicate_json_keys_fail_closed() -> None:
    """辅助引用不得泄漏后续答案，重复 JSON 字段不得覆盖提取结论。"""
    payload = result()
    payload["opportunities"][0]["supporting"] = [{"source_id": "response:10", "quote": "后来改为6000元"}]
    material = (*sources(), JudgeSource(id="response:10", turn=5, text="后来改为6000元"))
    for answer in (json.dumps(payload), '{"complete":true,"complete":false,"unresolved":[],"opportunities":[]}'):
        after = AsyncMock()
        output = await extract_opportunities(StubJudge(answer), material, through_turn=5,
            input_budget=10000, output_tokens=2048, before_call=AsyncMock(), after_call=after)
        assert output.status == "failed"
        assert after.await_args.args[0].usage.total_tokens == 20
