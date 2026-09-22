"""报告窗口按真实输入预算保存完整原文，缺少跨段关系时不能伪装全量评分。"""
import pytest

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgeSource, build_judge_messages
from app.services.multiturn.report_plan import build_report_plan


def test_short_report_uses_one_complete_window() -> None:
    """能够完整读取的短对话不人为切段或添加未知关系。"""
    sources = (JudgeSource(id="turn:1:user", turn=1, text="目标"), JudgeSource(id="response:1", turn=1, text="回答"))
    plan = build_report_plan(sources, 1, 10000)
    exact = estimate_tokens(build_judge_messages(plan.packet))
    assert len(build_report_plan(sources, 1, exact).batches) == 1
    assert plan.packet.sources == sources and not plan.limitations


def test_long_report_keeps_all_sources_and_marks_cross_window_unknown() -> None:
    """所有完整消息都进入某个有界窗口，原文覆盖完整不代表跨窗口推理完整。"""
    sources = tuple(JudgeSource(id=f"response:{index}", turn=index, text=f"第{index}轮" + "事实材料" * 240)
                    for index in range(1, 15))
    plan = build_report_plan(sources, 14, 7000)
    assert len(plan.batches) > 1 and plan.limitations
    assert {source.id: source.text for batch in plan.batches for source in batch.sources} == {source.id: source.text for source in sources}
    assert all(estimate_tokens(build_judge_messages(batch)) <= 7000 for batch in plan.batches)
    guards = [check for check in plan.packet.checks if check.id.startswith("cross_window:")]
    assert {item.dimension for item in guards} == {"goal", "memory", "consistency"}
    assert all(item.required_applicability == "unknown" for item in guards)


def test_unfit_message_and_future_source_fail_before_call() -> None:
    """单条资料不能被静默截断，未来原文也不能进入报告。"""
    with pytest.raises(ValueError, match="超过预算"):
        build_report_plan((JudgeSource(id="u1", turn=1, text="长原文" * 10000),), 1, 7000)
    with pytest.raises(ValueError, match="未来"):
        build_report_plan((JudgeSource(id="u2", turn=2, text="未来"),), 1, 7000)


def test_scoped_requirement_keeps_all_valid_range_sources_across_windows() -> None:
    """主报告很长时仍可独立核验范围内的全部原文，不强制降为未知。"""
    sources = tuple(JudgeSource(id=f"response:{index}", turn=index, text=f"第{index}轮" + "事实材料" * 240)
                    for index in range(1, 15))
    check = JudgeCheck(id="requirement:budget", requirement_id="budget", dimension="goal", description="第2至3轮预算限制", critical=True)
    plan = build_report_plan(sources, 14, 9000, (check,), check_sources={check.id: ("response:2", "response:3")})
    batch = next(batch for batch in plan.batches if any(item.id == check.id for item in batch.checks))
    assert [source.id for source in batch.sources] == ["response:2", "response:3"]
    assert len(batch.checks) == 1 and batch.checks[0].required_applicability is None
    assert batch.checks[0].allowed_source_ids == ("response:2", "response:3")
    assert len([item for item in plan.packet.checks if item.id == check.id]) == 1
    assert all(estimate_tokens(build_judge_messages(batch)) <= 9000 for batch in plan.batches)


def test_scoped_requirement_over_budget_remains_unknown_and_never_drops_global_guard() -> None:
    """完整要求范围不能装入时不截取部分回答冒充已验证。"""
    sources = tuple(JudgeSource(id=f"response:{index}", turn=index, text="事实材料" * 240) for index in range(1, 15))
    check = JudgeCheck(id="requirement:all", requirement_id="all", dimension="goal", description="全程预算要求")
    plan = build_report_plan(sources, 14, 7000, (check,), check_sources={check.id: tuple(source.id for source in sources)})
    item = next(item for item in plan.packet.checks if item.id == check.id)
    assert item.required_applicability == "unknown"
    assert any("requirement:all" in limitation for limitation in plan.limitations)
    assert any(item.id == "cross_window:goal" and item.required_applicability == "unknown" for item in plan.packet.checks)


def test_scope_mapping_rejects_foreign_sources_and_preserves_ambiguity() -> None:
    """范围来源必须来自总快照，歧义要求不能因装箱成功自动解除。"""
    sources = tuple(JudgeSource(id=f"response:{index}", turn=index, text="事实材料" * 240) for index in range(1, 15))
    check = JudgeCheck(id="requirement:unclear", requirement_id="unclear", dimension="goal", description="有歧义的要求", required_applicability="unknown")
    with pytest.raises(ValueError, match="范围"):
        build_report_plan(sources, 14, 7000, (check,), check_sources={check.id: ("foreign",)})
    plan = build_report_plan(sources, 14, 7000, (check,), check_sources={check.id: ("response:1",)})
    assert next(item for item in plan.packet.checks if item.id == check.id).required_applicability == "unknown"
