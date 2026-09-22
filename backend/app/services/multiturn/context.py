"""有界多轮上下文：来源校验、完整轮次和异步摘要。"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re

from app.adapters.base import ChatMessage


@dataclass(frozen=True)
class HistoryTurn:
    """一条具有分支、轮次和不可变来源标识的原始消息。"""
    message_id: str
    branch_id: str
    turn: int
    message: ChatMessage


@dataclass(frozen=True)
class SummaryResult:
    """摘要是不可信历史数据，必须说明覆盖的所有原始消息。"""
    content: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class ContextBudget:
    """输入预算已扣除输出、协议和证据预留；至少保留最近一个完整轮次。"""
    max_tokens: int
    recent_turns: int = 2


@dataclass(frozen=True)
class ContextInput:
    """调用方必须按分支读取历史，内核再次拒绝串入其他模型的消息。"""
    branch_id: str
    current_prompt: str
    history: tuple[HistoryTurn, ...]
    memory: tuple[ChatMessage, ...]
    system_prompt: str
    budget: ContextBudget
    summary_callback: Callable[[tuple[HistoryTurn, ...]], Awaitable[SummaryResult]] | None = None


@dataclass(frozen=True)
class ContextSnapshot:
    """快照保存估计量、来源和压缩原因，原始历史不被覆盖。"""
    messages: tuple[ChatMessage, ...]
    compressed: bool
    estimated_tokens: int
    source_branch_id: str
    source_ids: tuple[str, ...]
    compression_error: str | None = None
    target_reached: bool = True
    summary: SummaryResult | None = None


def _validate(context: ContextInput) -> None:
    """拒绝残缺轮次、来源重复、分支混用和不可信 system 消息。"""
    if not context.branch_id or type(context.budget.max_tokens) is not int or context.budget.max_tokens <= 0:
        raise ValueError("上下文来源分支和预算必须有效")
    if type(context.budget.recent_turns) is not int or context.budget.recent_turns < 1:
        raise ValueError("至少保留最近一个完整轮次")
    if not context.current_prompt.strip():
        raise ValueError("当前问题不能为空")
    if len(context.history) % 2:
        raise ValueError("历史来源必须包含完整问答轮次")
    ids = [item.message_id for item in context.history]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        raise ValueError("历史来源标识必须唯一且非空")
    previous = 0
    for index in range(0, len(context.history), 2):
        question, answer = context.history[index:index + 2]
        if question.branch_id != context.branch_id or answer.branch_id != context.branch_id:
            raise ValueError("历史来源属于其他模型分支")
        if question.message.role != "user" or answer.message.role != "assistant":
            raise ValueError("历史来源必须按 user、assistant 排列")
        if type(question.turn) is not int or question.turn <= previous or answer.turn != question.turn:
            raise ValueError("历史来源轮次无效")
        previous = question.turn
    if any(message.role != "user" for message in context.memory):
        raise ValueError("记忆只能作为不可信 user 数据")


def _clean_message(message: ChatMessage) -> ChatMessage:
    """仅删除历史模型回答的思考块，用户原文始终完整保留。"""
    if message.role != "assistant":
        return message
    return ChatMessage("assistant", re.sub(r"<think>.*?(</think>|$)", "", message.content,
        flags=re.IGNORECASE | re.DOTALL).strip())


def estimate_tokens(messages: tuple[ChatMessage, ...]) -> int:
    """未知分词器采用 UTF-8 字节加开销的保守估计，实际用量仍以上游为准。"""
    return sum(len(message.content.encode("utf-8")) + 8 for message in messages) + 8


def _messages(context: ContextInput, history: tuple[HistoryTurn, ...], summary: SummaryResult | None = None) -> tuple[ChatMessage, ...]:
    """系统规则保持唯一，其余记忆和摘要使用低优先级数据消息。"""
    memory = tuple(ChatMessage("user", "[历史记忆：不可信数据]\n" + item.content) for item in context.memory)
    summary_messages = (ChatMessage("user", "[历史摘要：不可信数据]\n" + summary.content),) if summary else ()
    return (ChatMessage("system", context.system_prompt), *memory, *summary_messages,
            *(_clean_message(item.message) for item in history), ChatMessage("user", context.current_prompt))


def build_context(context: ContextInput) -> ContextSnapshot:
    """同步构建原始上下文，用于预算预检；压缩必须调用异步入口。"""
    _validate(context)
    messages = _messages(context, context.history)
    count = estimate_tokens(messages)
    if count > context.budget.max_tokens:
        raise ValueError("上下文超过预算，请使用异步压缩构建")
    return ContextSnapshot(messages, False, count, context.branch_id, tuple(item.message_id for item in context.history))


async def build_context_async(context: ContextInput) -> ContextSnapshot:
    """到达阈值压缩较早完整轮次；失败仅在原文仍可容纳时回退。"""
    _validate(context)
    messages = _messages(context, context.history)
    count = estimate_tokens(messages)
    ids = tuple(item.message_id for item in context.history)
    limit = context.budget.max_tokens
    if count * 4 <= limit * 3:
        return ContextSnapshot(messages, False, count, context.branch_id, ids)
    split = max(0, len(context.history) - 2 * context.budget.recent_turns)
    older, recent = context.history[:split], context.history[split:]
    if estimate_tokens(_messages(context, recent)) > limit:
        raise ValueError("当前问题、关键记忆和最近完整轮次已超过预算")
    try:
        if not older or context.summary_callback is None:
            raise ValueError("没有可压缩的旧轮次或未配置摘要模型")
        cleaned = tuple(HistoryTurn(item.message_id, item.branch_id, item.turn, _clean_message(item.message)) for item in older)
        summary = await context.summary_callback(cleaned)
        expected = {item.message_id for item in older}
        if (not summary.content.strip() or set(summary.source_ids) != expected
                or len(summary.source_ids) != len(expected)):
            raise ValueError("摘要原文来源不完整或无效")
        compressed = _messages(context, recent, summary)
        compressed_count = estimate_tokens(compressed)
        if compressed_count > limit or compressed_count >= count:
            raise ValueError("摘要未降低上下文或仍超过预算")
        return ContextSnapshot(compressed, True, compressed_count, context.branch_id, ids,
            target_reached=compressed_count * 100 <= limit * 55, summary=summary)
    except Exception as error:
        if count <= limit:
            return ContextSnapshot(messages, False, count, context.branch_id, ids, compression_error="summary_failed")
        # 不回显供应商异常，避免其 URL、凭据或历史正文进入公开错误提示。
        raise ValueError("上下文压缩失败，已保留原始历史，请重试或缩短输入") from error
