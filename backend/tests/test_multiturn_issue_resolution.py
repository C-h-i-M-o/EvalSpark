"""解决状态必须具有后续原文依据，且不能篡改问题或报告截止范围。"""
import json
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.services.multiturn.issue_resolution import (
    aggregate_resolution, build_resolution_packet, parse_resolution, resolution_messages,
)
from app.services.multiturn.judge import JudgeSource
from app.adapters.base import ModelReply, ModelUsage
from app.services.multiturn.issue_resolution import evaluate_resolution
from test_multiturn_judge import StubJudge


def material():
    """构造历史失败、纠正、修正及再次错误的连续原文。"""
    sources = (JudgeSource(id="user:1", turn=1, text="预算100"),
        JudgeSource(id="response:1", turn=1, text="预算不限"),
        JudgeSource(id="turn:2:user", turn=2, text="请改回100"),
        JudgeSource(id="response:2", turn=2, text="已改回100"),
        JudgeSource(id="response:3", turn=3, text="最终预算不限"))
    return build_resolution_packet("issue:budget", ("user:1", "response:1"), sources, 3,
        description="回答未遵守预算100的约束")


def verdict(status="resolved", extra=None):
    """生成包含历史失败及后续模型修正引文的标准响应。"""
    return json.dumps({"issue_id": "issue:budget", "status": status, "reason": "依据后续原文",
        "evidence": [{"source_id": "response:1", "quote": "预算不限"},
                     extra or {"source_id": "response:2", "quote": "已改回100"}]})


def test_resolution_reads_entire_interval_and_derives_reference_turns() -> None:
    """完整请求保留后来再次犯错的原文，不只挑选一次修正。"""
    packet = material()
    messages = resolution_messages(packet, 10000)
    assert "最终预算不限" in messages[-1].content and "请改回100" in messages[-1].content
    assert json.loads(messages[-1].content)["description"] == "回答未遵守预算100的约束"
    result = parse_resolution(verdict(), packet)
    assert result.evidence[1].turn == 2 and result.through_turn == 3
    assert packet.sources[-1].id == "response:3"
    with pytest.raises(ValueError, match="预算"):
        resolution_messages(packet, 1)


@pytest.mark.parametrize("response", [
    verdict(extra={"source_id": "missing", "quote": "已改回100"}),
    verdict(extra={"source_id": "response:2", "quote": "虚构修正"}),
    verdict(extra={"source_id": "response:1", "quote": "预算不限"}),
    verdict("superseded"),
    verdict().replace("issue:budget", "issue:other"),
])
def test_false_resolution_or_wrong_identity_is_rejected(response: str) -> None:
    """拒绝伪造引文、同轮自称修正、模型自行撤销和串问题结果。"""
    with pytest.raises(ValueError):
        parse_resolution(response, material())


def test_supersession_requires_user_and_unknown_can_have_no_evidence() -> None:
    """用户后续变更具有独立状态；缺证据未知不伪装为尚未解决。"""
    packet = material()
    packet = replace(packet, sources=tuple(source.model_copy(update={"text": "取消预算限制"})
        if source.id == "turn:2:user" else source for source in packet.sources))
    superseded = parse_resolution(verdict("superseded", {"source_id": "turn:2:user", "quote": "取消预算限制"}), packet)
    assert superseded.status == "superseded"
    unknown = parse_resolution(json.dumps({"issue_id": packet.issue_id, "status": "unknown", "reason": "依据不足", "evidence": []}), packet)
    assert unknown.status == "unknown" and unknown.evidence == ()


def test_user_requirement_alone_cannot_replace_failed_answer_evidence() -> None:
    """只引用原要求和后续回答，不能证明所追踪的历史失败。"""
    response = json.loads(verdict())
    response["evidence"][0] = {"source_id": "user:1", "quote": "预算100"}
    with pytest.raises(ValueError, match="历史失败"):
        parse_resolution(json.dumps(response), material())


def test_resolution_consensus_preserves_disagreement_and_scope() -> None:
    """三组分歧或不足两组不形成解决结论，不跨截止范围聚合。"""
    packet = material()
    resolved = parse_resolution(verdict(), packet)
    assert aggregate_resolution((resolved, resolved, None), packet) == "resolved"
    assert aggregate_resolution((resolved, None, None), packet) == "unknown"
    assert aggregate_resolution((resolved, resolved, replace(resolved, status="unresolved")), packet) == "unknown"
    with pytest.raises(ValueError):
        aggregate_resolution((resolved, resolved, replace(resolved, through_turn=4)), packet)


def test_resolution_packet_rejects_future_missing_and_duplicate_sources() -> None:
    """失败来源必须含回答，未来轮次和重复消息不进入固定快照。"""
    packet = material()
    for ids, sources, through in ((("user:1",), packet.sources, 3),
        (("missing",), packet.sources, 3), (packet.failure_source_ids, packet.sources, 2),
        (packet.failure_source_ids, (*packet.sources, packet.sources[-1]), 3)):
        with pytest.raises(ValueError):
            build_resolution_packet(packet.issue_id, ids, sources, through, description=packet.description)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,valid", [(verdict(), True), ("无效JSON", False)])
async def test_resolution_call_settles_before_parsing_even_when_invalid(answer: str, valid: bool) -> None:
    """正常和无效输出均恰好结算一次，解析不能丢失已发生的用量。"""
    client = StubJudge(answer)
    reply = ModelReply(answer, ModelUsage(10, 5), 1)
    client.chat = AsyncMock(return_value=reply)
    before, after = AsyncMock(), AsyncMock()
    result = await evaluate_resolution(client, material(), input_budget=10000, output_tokens=2048,
        before_call=before, after_call=after)
    before.assert_awaited_once()
    after.assert_awaited_once_with(reply)
    client.chat.assert_awaited_once()
    assert (result.result is not None) == valid and result.reply == reply
    assert result.error_code == (None if valid else "resolution_invalid")
    request = client.chat.call_args.args[0]
    assert request.max_tokens == 2048 and "最终预算不限" in request.messages[-1].content


@pytest.mark.asyncio
async def test_resolution_budget_and_registration_fail_before_paid_request() -> None:
    """预算不足或登记失败时不调用供应商，也不执行未开始请求的结算。"""
    client = StubJudge(verdict())
    client.chat = AsyncMock()
    before, after = AsyncMock(), AsyncMock()
    with pytest.raises(ValueError, match="预算"):
        await evaluate_resolution(client, material(), input_budget=1, output_tokens=2048,
            before_call=before, after_call=after)
    before.assert_not_awaited()
    before.side_effect = RuntimeError("登记失败")
    with pytest.raises(RuntimeError, match="登记失败"):
        await evaluate_resolution(client, material(), input_budget=10000, output_tokens=2048,
            before_call=before, after_call=after)
    client.chat.assert_not_awaited()
    after.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolution_network_error_and_cancellation_settle_unknown_without_retry() -> None:
    """网络失败返回无效结果，取消继续传播，两者均结算未知用量且不重试。"""
    for error in (RuntimeError("连接失败"), asyncio.CancelledError()):
        client = StubJudge(verdict())
        client.chat = AsyncMock(side_effect=error)
        before, after = AsyncMock(), AsyncMock()
        if isinstance(error, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await evaluate_resolution(client, material(), input_budget=10000, output_tokens=2048,
                    before_call=before, after_call=after)
        else:
            result = await evaluate_resolution(client, material(), input_budget=10000, output_tokens=2048,
                before_call=before, after_call=after)
            assert result.error_code == "resolution_call_failed" and result.reply is None
        after.assert_awaited_once_with(None)
        client.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolution_accounting_failure_is_not_hidden_by_valid_answer() -> None:
    """即使模型结果合法，入账失败也必须向协调器传播。"""
    client = StubJudge(verdict())
    client.chat = AsyncMock(return_value=ModelReply(verdict(), ModelUsage(10, 5), 1))
    with pytest.raises(RuntimeError, match="入账失败"):
        await evaluate_resolution(client, material(), input_budget=10000, output_tokens=2048,
            before_call=AsyncMock(), after_call=AsyncMock(side_effect=RuntimeError("入账失败")))
    client.chat.assert_awaited_once()
