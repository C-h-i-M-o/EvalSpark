"""摘要调用覆盖分批预算、来源核验与失败用量回调。"""
import json
from decimal import Decimal
from unittest.mock import AsyncMock
from typing import Literal

import pytest

from app.adapters.base import ChatMessage, ModelClient, ModelReply, ModelRequest, ModelUsage
from app.services.multiturn.context import HistoryTurn, estimate_tokens
from app.services.multiturn.summary import ModelSummarizer


class SummaryClient(ModelClient):
    """将收到的原文转换为可校验记忆，记录每个实际输入。"""

    def __init__(self, invalid: bool = False) -> None:
        """切换伪造引用场景。"""
        self.invalid = invalid
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelReply:
        """返回含原始用户要求的短摘要。"""
        self.requests.append(request)
        data = json.loads(request.messages[1].content)
        previous = data["previous"]
        first = data["history"][0]
        facts = previous["facts"] if previous else [{"kind": "requirement", "text": "保留用户条件",
            "source_id": first["id"], "quote": first["content"][:6]}]
        if self.invalid:
            facts[0]["quote"] = "并不存在的原文"
        return ModelReply(json.dumps({"summary": "已讨论用户条件", "facts": facts}, ensure_ascii=False), ModelUsage(7, 3), 1)

    def get_model_name(self) -> str:
        """返回测试模型名。"""
        return "summary"

    def estimate_cost(self, usage: ModelUsage) -> Decimal:
        """不调用真实收费模型。"""
        return Decimal("0")


def history(role: Literal["user", "assistant"] = "user") -> tuple[HistoryTurn, ...]:
    """首条原文本身超过摘要预算，必须切片读取。"""
    return (HistoryTurn("u1", "a", 1, ChatMessage(role, "预算7000元。" + "其他背景" * 1500)),
            HistoryTurn("a1", "a", 1, ChatMessage("assistant", "已了解条件")))


@pytest.mark.asyncio
async def test_long_history_is_consumed_with_bounded_model_requests() -> None:
    """单条超长原文也分批完整消费，所有请求均在输入预算内。"""
    client = SummaryClient()
    before, after = AsyncMock(), AsyncMock()
    summarizer = ModelSummarizer(client, input_budget=3000, output_tokens=500, memory_budget=600,
                                before_call=before, after_call=after)
    source = history()
    result = await summarizer(source)
    assert len(client.requests) > 1
    assert all(estimate_tokens(request.messages) <= 3000 for request in client.requests)
    restored: dict[str, str] = {}
    for request in client.requests:
        for part in json.loads(request.messages[1].content)["history"]:
            restored[part["id"]] = restored.get(part["id"], "") + part["content"]
    assert restored == {item.message_id: item.message.content for item in source}
    assert result.source_ids == ("u1", "a1")
    assert before.await_count == after.await_count == len(client.requests)
    assert all(call.args[1].usage.total_tokens == 10 for call in after.await_args_list)


@pytest.mark.asyncio
async def test_fabricated_reference_reports_usage_then_fails() -> None:
    """来源无效仍报告已发生调用，不能悄悄抹掉费用。"""
    after = AsyncMock()
    summarizer = ModelSummarizer(SummaryClient(invalid=True), input_budget=3000, output_tokens=500,
                                memory_budget=600, before_call=AsyncMock(), after_call=after)
    with pytest.raises(ValueError, match="原文"):
        await summarizer(history())
    assert after.await_count == 1 and after.await_args.args[1] is not None


@pytest.mark.asyncio
async def test_assistant_cannot_be_source_of_user_requirement() -> None:
    """摘要不能把历史模型建议升级为用户约束。"""
    summarizer = ModelSummarizer(SummaryClient(), input_budget=3000, output_tokens=500,
                                memory_budget=600, before_call=AsyncMock(), after_call=AsyncMock())
    with pytest.raises(ValueError, match="冒充"):
        await summarizer(history(role="assistant"))


@pytest.mark.asyncio
async def test_insufficient_budget_fails_before_paid_call() -> None:
    """连协议都无法容纳时不能发起模型请求。"""
    client = SummaryClient()
    before = AsyncMock()
    summarizer = ModelSummarizer(client, input_budget=10, output_tokens=500, memory_budget=600,
                                before_call=before, after_call=AsyncMock())
    with pytest.raises(ValueError, match="预算"):
        await summarizer(history())
    assert client.requests == []
    before.assert_not_called()


@pytest.mark.asyncio
async def test_provider_exception_reports_unknown_usage_without_retry() -> None:
    """上游异常也执行未知用量回调，不隐式重试收费调用。"""
    client = SummaryClient()
    client.chat = AsyncMock(side_effect=RuntimeError("模拟调用失败"))
    after = AsyncMock()
    summarizer = ModelSummarizer(client, input_budget=3000, output_tokens=500, memory_budget=600,
                                before_call=AsyncMock(), after_call=after)
    with pytest.raises(RuntimeError):
        await summarizer(history())
    assert client.chat.await_count == 1
    after.assert_awaited_once_with(1, None)
