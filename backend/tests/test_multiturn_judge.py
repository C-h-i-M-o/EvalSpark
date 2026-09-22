"""评审边界验证固定检查项及原文来源，避免模型自报分数。"""
import asyncio
import json
from decimal import Decimal

import pytest

from app.adapters.base import ModelClient, ModelReply, ModelRequest, ModelUsage
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource, evaluate_packet


class StubJudge(ModelClient):
    """捕获实际模型请求并返回确定性的供应商响应。"""

    def __init__(self, answer: str, fail: bool = False) -> None:
        """保存响应与异常开关。"""
        self.answer = answer
        self.fail = fail
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelReply:
        """模拟一次已知用量的真实调用边界。"""
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("敏感供应商错误")
        return ModelReply(self.answer, ModelUsage(12, 8), 10)

    def get_model_name(self) -> str:
        """返回测试模型名称。"""
        return "judge"

    def estimate_cost(self, usage: ModelUsage) -> Decimal:
        """返回确定性费用。"""
        return Decimal("0")


def packet() -> JudgePacket:
    """提供固定检查项和截至当前轮的原始用户消息。"""
    return JudgePacket(
        scope="dialogue", through_turn=2, answer="预算为7000元。",
        sources=(JudgeSource(id="u2", turn=2, text="预算改为7000元"),),
        checks=(JudgeCheck(id="budget", dimension="instruction", description="遵守新预算",
                           requirement_id="r2", critical=True),),
    )


def verdict() -> dict[str, object]:
    """构造引用原文而非自由编造证据的检查项判定。"""
    return {"items": [{"id": "budget", "applicability": "applicable", "rating": 4,
                       "passed": True, "reason": "采用新预算", "evidence": [
                           {"source_id": "u2", "quote": "7000元"},
                           {"source_id": "answer", "quote": "7000元"}]}]}


@pytest.mark.asyncio
async def test_grounded_verdict_uses_server_owned_metadata() -> None:
    """真实引用通过校验，模型不能决定维度、关键性或权重。"""
    client = StubJudge(json.dumps(verdict(), ensure_ascii=False))
    result = await evaluate_packet(client, packet(), input_budget=10000)
    assert result.status == "provisional"
    assert result.items[0].requirement_id == "r2"
    assert result.items[0].critical is True
    assert [reference.model_dump() for reference in result.items[0].evidence_refs] == [
        {"source_id": "u2", "turn": 2, "quote": "7000元"},
        {"source_id": "answer", "turn": 2, "quote": "7000元"},
    ]
    assert result.score.dimensions["instruction"] == Decimal("10")
    assert result.score.final is None  # 未提供其他维度，覆盖不完整。
    assert result.reply.usage.total_tokens == 20
    assert client.requests[0].messages[0].role == "system"


@pytest.mark.asyncio
async def test_requirement_references_cannot_use_other_turn_even_if_quote_exists() -> None:
    """短报告共享上下文也不能为旧要求引用范围外的新回答。"""
    original = packet()
    restricted = original.model_copy(update={"checks": (original.checks[0].model_copy(update={"allowed_source_ids": ("u2",)}),)})
    client = StubJudge(json.dumps(verdict()))
    result = await evaluate_packet(client, restricted, input_budget=10000)
    assert result.status == "judge_failed"  # answer 在全局上下文存在，但不属于该检查项允许范围。
    allowed = verdict()
    allowed["items"][0]["evidence"] = [{"source_id": "u2", "quote": "7000元"}]
    valid = await evaluate_packet(StubJudge(json.dumps(allowed)), restricted, input_budget=10000)
    assert valid.status == "provisional"


@pytest.mark.asyncio
async def test_reference_turn_comes_from_snapshot_not_source_name_or_model() -> None:
    """同一引文可出现多轮，轮次必须按指定来源查表，不解析编号或接受自报。"""
    source_packet = packet().model_copy(update={"sources": (JudgeSource(id="u2", turn=1, text="预算改为7000元"),)})
    result = await evaluate_packet(StubJudge(json.dumps(verdict())), source_packet, input_budget=10000)
    assert [reference.turn for reference in result.items[0].evidence_refs] == [1, 2]
    forged = verdict()
    forged["items"][0]["evidence"][0]["turn"] = 99
    invalid = await evaluate_packet(StubJudge(json.dumps(forged)), source_packet, input_budget=10000)
    assert invalid.status == "judge_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["quote", "source", "extra", "missing", "duplicate", "boolean"])
async def test_invalid_verdict_retains_billed_reply(mutation: str) -> None:
    """伪造证据、篡改固定项和非法 JSON 类型都失败但保留已消耗用量。"""
    data = verdict()
    item = data["items"][0]
    if mutation == "quote":
        item["evidence"][0]["quote"] = "预算无限"
    elif mutation == "source":
        item["evidence"][0]["source_id"] = "u99"
    elif mutation == "extra":
        item["dimension"] = "expression"
    elif mutation == "missing":
        data["items"] = []
    elif mutation == "duplicate":
        data["items"].append(item)
    else:
        item["rating"] = True
    client = StubJudge(json.dumps(data, ensure_ascii=False))
    result = await evaluate_packet(client, packet(), input_budget=10000)
    assert result.status == "judge_failed"
    assert result.error_code == "invalid_judge_output"
    assert result.reply.usage.total_tokens == 20
    assert result.items == ()


@pytest.mark.asyncio
async def test_budget_rejection_never_calls_provider() -> None:
    """输入超预算在收费调用前终止。"""
    client = StubJudge("{}")
    with pytest.raises(ValueError, match="预算"):
        await evaluate_packet(client, packet(), input_budget=10)
    assert client.requests == []


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_without_retry() -> None:
    """供应商错误不泄露内容、不自动产生额外费用、不伪造用量。"""
    client = StubJudge("{}", fail=True)
    result = await evaluate_packet(client, packet(), input_budget=10000)
    assert result.error_code == "judge_call_failed"
    assert result.reply is None
    assert len(client.requests) == 1


def test_future_evidence_rejected() -> None:
    """评审快照不能包含未来轮次证据。"""
    with pytest.raises(ValueError, match="未来"):
        packet().model_copy(update={"sources": (JudgeSource(id="u3", turn=3, text="未来"),)}).validate_sources()


@pytest.mark.asyncio
async def test_duplicate_json_keys_rejected() -> None:
    """重复键不能通过后写覆盖来绕过检查。"""
    client = StubJudge('{"items": [], "items": []}')
    result = await evaluate_packet(client, packet(), input_budget=10000)
    assert result.error_code == "invalid_judge_output"
    assert result.reply is not None


@pytest.mark.asyncio
async def test_judge_cannot_guess_ambiguous_requirement_applicability() -> None:
    """服务端未解析的歧义不能被 Judge 猜成适用并打满分。"""
    snapshot = packet().model_copy(update={"checks": (JudgeCheck(id="budget", dimension="instruction",
        description="歧义条件", required_applicability="unknown"),)})
    result = await evaluate_packet(StubJudge(json.dumps(verdict())), snapshot, input_budget=10000)
    assert result.error_code == "invalid_judge_output"


@pytest.mark.asyncio
async def test_session_packet_preserves_critical_failure() -> None:
    """会话级评分也保留关键失败，不通过平均分隐藏。"""
    snapshot = packet().model_copy(update={
        "scope": "session", "checks": (JudgeCheck(
            id="budget", dimension="goal", description="任务达到预算条件", critical=True),),
        "sources": (*packet().sources, JudgeSource(id="response:2", turn=2, text="预算为7000元。")),
    })
    data = verdict()
    data["items"][0]["passed"] = False
    data["items"][0]["rating"] = 0
    data["items"][0]["evidence"][1]["source_id"] = "response:2"
    result = await evaluate_packet(StubJudge(json.dumps(data)), snapshot, input_budget=10000)
    assert result.score.critical_passed is False
    assert result.score.dimensions["goal"] == 0


@pytest.mark.asyncio
async def test_session_report_description_cannot_be_used_as_original_evidence() -> None:
    """会话范围说明不是候选原文，Judge 不能引用 answer 冒充原始回答。"""
    snapshot = packet().model_copy(update={"scope": "session", "checks": (
        JudgeCheck(id="budget", dimension="goal", description="检查目标", critical=True),)})
    result = await evaluate_packet(StubJudge(json.dumps(verdict())), snapshot, input_budget=10000)
    assert result.error_code == "invalid_judge_output"


@pytest.mark.asyncio
async def test_cancellation_propagates() -> None:
    """任务取消必须向上传播，不能伪装成已完成的评审失败。"""
    class CancelledJudge(StubJudge):
        """模拟正在等待供应商时被取消。"""

        async def chat(self, request: ModelRequest) -> ModelReply:
            """向调度层传递取消。"""
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await evaluate_packet(CancelledJudge(""), packet(), input_budget=10000)
