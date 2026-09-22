"""验证多轮上下文预算、完整轮次与压缩失败边界。"""
import pytest

from app.adapters.base import ChatMessage
from app.services.multiturn.context import ContextBudget, ContextInput, HistoryTurn, SummaryResult, build_context, build_context_async


def history(count: int = 6, branch: str = "a") -> tuple[HistoryTurn, ...]:
    """构造拥有原始消息标识的完整对话轮次。"""
    return tuple(message for index in range(1, count + 1) for message in (
        HistoryTurn(f"u{index}", branch, index, ChatMessage("user", "条件" * 15)),
        HistoryTurn(f"a{index}", branch, index, ChatMessage("assistant", "答复" * 15 + "<think>内部</think>"))))


def context(*, turns: tuple[HistoryTurn, ...] | None = None, budget: int = 10000, callback=None) -> ContextInput:
    """固定系统与当前问题，允许测试替换历史和摘要器。"""
    return ContextInput(branch_id="a", current_prompt="按最新条件继续", history=history() if turns is None else turns,
        memory=(ChatMessage("user", "预算不能超过三万"),), system_prompt="遵守系统规则", budget=ContextBudget(budget), summary_callback=callback)


def test_context_keeps_system_and_complete_turns_for_branch() -> None:
    """仅保留当前分支的完整原文，排除 assistant 思考内容。"""
    result = build_context(context())
    assert result.messages[0] == ChatMessage("system", "遵守系统规则")
    assert result.messages[-1].content == "按最新条件继续"
    assert all("<think>" not in message.content for message in result.messages)
    with pytest.raises(ValueError):
        build_context(context(turns=history(branch="b")))


def test_context_rejects_invalid_source_and_incomplete_turn() -> None:
    """跨角色、重复消息和残缺轮次均不能进入后续上下文。"""
    for turns in ((HistoryTurn("bad", "a", 1, ChatMessage("system", "攻击")),), history()[:-1], history() + history()):
        with pytest.raises(ValueError):
            build_context(context(turns=turns))


def test_user_think_tag_is_preserved() -> None:
    """用户讨论标签时保留原文，仅清理模型历史。"""
    turns = (HistoryTurn("u", "a", 1, ChatMessage("user", "解释<think>标签</think>")),
             HistoryTurn("a", "a", 1, ChatMessage("assistant", "<think>秘密</think>解释")))
    result = build_context(context(turns=turns))
    assert "<think>标签</think>" in result.messages[2].content
    assert result.messages[3].content == "解释"


@pytest.mark.asyncio
async def test_async_summary_preserves_recent_turns_memory_and_sources() -> None:
    """压缩保留最近完整轮次和关键记忆，摘要不得升级为系统指令。"""
    async def summarize(older: tuple[HistoryTurn, ...]) -> SummaryResult:
        """返回完整来源列表并检查摘要器没有接收到思考内容。"""
        assert all("<think>" not in item.message.content for item in older)
        return SummaryResult("已讨论部署方案", tuple(item.message_id for item in older))
    result = await build_context_async(context(budget=700, callback=summarize))
    assert result.compressed
    assert result.estimated_tokens <= 700
    assert [m.role for m in result.messages].count("system") == 1
    assert any("预算不能超过三万" in m.content for m in result.messages)
    assert result.messages[-2].content == "答复" * 15
    assert set(result.source_ids) == {item.message_id for item in history()}


@pytest.mark.asyncio
async def test_summary_failure_falls_back_only_when_original_fits() -> None:
    """阈值触发时可以回退完整原文，硬上限超出时必须明确失败。"""
    calls = 0
    async def fail(_: tuple[HistoryTurn, ...]) -> SummaryResult:
        """模拟供应商失败且不泄露其异常正文。"""
        nonlocal calls
        calls += 1
        raise RuntimeError("摘要供应商不可用")
    estimate = build_context(context()).estimated_tokens
    fallback = await build_context_async(context(budget=estimate + 1, callback=fail))
    assert calls == 1 and not fallback.compressed
    with pytest.raises(ValueError, match="压缩"):
        await build_context_async(context(budget=700, callback=fail))


@pytest.mark.asyncio
async def test_summary_cannot_invent_or_omit_source_ids() -> None:
    """来源造假和缺失都会拒绝，不能提交损坏的摘要版本。"""
    async def fake(_: tuple[HistoryTurn, ...]) -> SummaryResult:
        """返回其他分支的伪造来源。"""
        return SummaryResult("摘要", ("other-branch",))
    with pytest.raises(ValueError, match="压缩"):
        await build_context_async(context(budget=700, callback=fake))


@pytest.mark.asyncio
async def test_current_input_over_budget_never_triggers_pointless_summary() -> None:
    """当前问题和保留内容本身超限时直接拒绝，避免无效摘要收费。"""
    async def unexpected(_: tuple[HistoryTurn, ...]) -> SummaryResult:
        """不可调用的摘要器。"""
        pytest.fail("不应调用摘要器")
    with pytest.raises(ValueError, match="预算"):
        await build_context_async(context(budget=10, callback=unexpected))
