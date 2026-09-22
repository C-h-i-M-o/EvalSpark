"""隔离 MySQL 验证报告准备登记、用量与原文快照边界。"""
from decimal import Decimal
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelUsage
from app.models.conversation import ConversationUsage
from app.services.multiturn.assessments import AssessmentStore
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource
from app.services.multiturn.opportunities import OpportunityResult
from app.services.multiturn.preparation import PreparationStore, execute_preparation
from app.services.multiturn.store import ConversationError
from app.services.multiturn.opportunity_packets import build_opportunity_packets
from app.models.conversation import ConversationAssessment
from test_multiturn_assessments import FixedJudge, setup_job


@pytest.mark.asyncio
async def test_boundary_preparation_bills_once_and_freezes_three_review_groups(rag_sessions, rag_users) -> None:
    """两原窗加一边界分别入账，正式只计跨界机会，重复投递不重复收费。"""
    from test_multiturn_report_bridges import windows
    from test_multiturn_reports import ReportJudge
    from app.services.multiturn.store import ConversationStore
    from app.services.multiturn.assessments import execute_assessment
    from app.models.conversation import ConversationJudgeRun
    owner = rag_users[0]
    conversation_id, _, _ = await setup_job(rag_sessions, owner)
    batches = windows()
    packet = batches[0].model_copy(update={"sources": (*batches[0].sources, *batches[1].sources),
        "checks": tuple(check for batch in batches for check in batch.checks)})
    async with rag_sessions() as db:
        for index in range(2, 5):
            turn, _ = await ConversationStore().reserve_turn(db, conversation_id, owner, prompt=f"第{index}轮",
                expected_turn=index - 1, request_key=f"boundary-{index}")
            await ConversationStore().finish_generation(db, conversation_id, turn.id, owner, status="completed")
        job = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="boundary-report",
            model_config_id=1, packet=packet, batches=batches, formal=True, input_budget=10000, currency="CNY",
            report_metadata={"semanticPreparation": 2, "limitations": []})
        job_id = job.id

    class BoundaryJudge(ReportJudge):
        """原窗口返回空清单，边界同时返回跨界与单窗机会以验证过滤。"""

        async def chat(self, request):
            """保留实际解析链路，所有测试调用都返回已知用量。"""
            data = json.loads(request.messages[-1].content)
            if "checks" in data:
                return await super().chat(request)
            self.calls += 1
            opportunities = []
            if len(data["sources"]) == 4:
                target = {"source_id": "response:4", "quote": "最终采用200"}
                opportunities = [
                    {"dimension": "memory", "description": "检查条件修改后的记忆", "trigger": {"source_id": "user:1", "quote": "预算100"}, "target": target, "supporting": []},
                    {"dimension": "goal", "description": "单窗重复机会", "trigger": {"source_id": "user:3", "quote": "改为200"}, "target": target, "supporting": []},
                ]
            return ModelReply(json.dumps({"complete": True, "unresolved": [], "opportunities": opportunities}), ModelUsage(10, 5), 1)

    judge = BoundaryJudge()
    assert await execute_assessment(rag_sessions, job_id, owner, judge)
    assert judge.calls == 15  # 三次准备，三组各含三个覆盖审核及一个跨界机会。
    async with rag_sessions() as db:
        fixed = await db.get(ConversationAssessment, job_id)
        assert fixed.status == "scored"
        assert fixed.input_json["preparationFrozen"] is True
        assert fixed.input_json["originalReportInput"]["packet"] == packet.model_dump(mode="json")
        assert fixed.input_json["report"]["semanticOpportunityCount"] == 1
        usages = (await db.scalars(select(ConversationUsage).where(
            ConversationUsage.operation_key.like(f"assessment:{job_id}:%")))).all()
        preparation = [usage for usage in usages if usage.stage == "opportunity"]
        assert len(preparation) == 3 and len(usages) == 15
        assert sum(usage.total_tokens for usage in usages) == 225 and all(usage.accounted for usage in usages)
        boundary = next(usage for usage in preparation if usage.detail_json["preparationIndex"] == 3)
        assert len(boundary.detail_json["result"]["opportunities"]) == 2
        runs = (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id))).all()
        assert len(runs) == 12
    assert not await execute_assessment(rag_sessions, job_id, owner, judge)
    assert judge.calls == 15


async def report_job(sessions, owner: int) -> tuple[int, tuple[JudgeSource, ...]]:
    """建立隔离正式报告并认领，准备阶段不会调用真实供应商。"""
    conversation_id, _, _ = await setup_job(sessions, owner)
    sources = (JudgeSource(id="turn:1:user", turn=1, text="预算要求"),
               JudgeSource(id="response:1", turn=1, text="预算回答"))
    packet = JudgePacket(scope="session", through_turn=1, answer="会话报告", sources=sources,
        checks=(JudgeCheck(id="goal", dimension="goal", description="检查"),))
    async with sessions() as db:
        job = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="report:1:1",
            model_config_id=1, packet=packet, formal=True, input_budget=10000, currency="CNY",
            report_metadata={"throughTurn": 1})
        await AssessmentStore().claim(db, job.id, owner)
        return job.id, sources


@pytest.mark.asyncio
async def test_preparation_is_idempotent_billed_and_preserves_invalid_results(rag_sessions, rag_users) -> None:
    """无效输出仍入账，重复登记和重复结算不会再次扣费或覆盖结果。"""
    owner = rag_users[0]
    job_id, sources = await report_job(rag_sessions, owner)
    store = PreparationStore()
    async with rag_sessions() as db:
        assert await store.begin(db, job_id, owner, 1, sources)
        assert not await store.begin(db, job_id, owner, 1, sources)
        await store.save_usage(db, job_id, owner, 1, ModelReply("无效输出", ModelUsage(10, 5), 1), FixedJudge())
        await store.save_result(db, job_id, owner, 1, OpportunityResult("failed", error_code="opportunity_invalid"))
        await store.save_usage(db, job_id, owner, 1, None, FixedJudge())
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.operation_key == f"assessment:{job_id}:opportunity:1"))
        assert usage.accounted and usage.total_tokens == 15 and usage.cost == Decimal("0.01")
        assert usage.detail_json["result"]["status"] == "failed"
        assert usage.detail_json["sourceIds"] == [source.id for source in sources]
        assert usage.detail_json["snapshotHash"]


@pytest.mark.asyncio
async def test_preparation_rejects_foreign_snapshot_and_late_writes(rag_sessions, rag_users) -> None:
    """非作者、变更来源和正式评分后的提取被拒绝，中断保留未知且阻止迟到写入。"""
    owner, reader = rag_users[:2]
    job_id, sources = await report_job(rag_sessions, owner)
    store = PreparationStore()
    for actor, selected in ((reader, sources), (owner, (sources[0].model_copy(update={"text": "伪造"}),))):
        async with rag_sessions() as db:
            with pytest.raises(ConversationError):
                await store.begin(db, job_id, actor, 1, selected)
    async with rag_sessions() as db:
        assert await store.begin(db, job_id, owner, 1, sources)
        with pytest.raises(ConversationError, match="准备结果"):
            await AssessmentStore().begin_run(db, job_id, owner, 1)
        await db.rollback()
        await AssessmentStore().interrupt(db, job_id, owner)
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.operation_key == f"assessment:{job_id}:opportunity:1"))
        assert usage.status == "unknown" and usage.total_tokens is None
        with pytest.raises(ConversationError):
            await store.save_usage(db, job_id, owner, 1, ModelReply("迟到", ModelUsage(10, 5), 1), FixedJudge())
    other, other_sources = await report_job(rag_sessions, owner)
    async with rag_sessions() as db:
        await AssessmentStore().begin_run(db, other, owner, 1)
        with pytest.raises(ConversationError):
            await store.begin(db, other, owner, 1, other_sources)


@pytest.mark.asyncio
async def test_extractor_callbacks_persist_once_and_reject_repeated_calls(rag_sessions, rag_users) -> None:
    """使用实际提取解析链路保存结果，重复执行在供应商调用前拒绝。"""
    owner = rag_users[0]
    job_id, sources = await report_job(rag_sessions, owner)
    client = FixedJudge()
    payload = {"complete": True, "unresolved": [], "opportunities": [{"dimension": "goal",
        "description": "完成预算要求", "trigger": {"source_id": sources[0].id, "quote": "预算要求"},
        "target": {"source_id": sources[1].id, "quote": "预算回答"}, "supporting": []}]}
    client.chat = AsyncMock(return_value=ModelReply(json.dumps(payload), ModelUsage(10, 5), 1))
    result = await execute_preparation(rag_sessions, job_id, owner, 1, sources, client,
        through_turn=1, input_budget=10000, output_tokens=2048)
    assert result.status == "ready"
    with pytest.raises(ConversationError, match="不能自动重发"):
        await execute_preparation(rag_sessions, job_id, owner, 1, sources, client,
            through_turn=1, input_budget=10000, output_tokens=2048)
    client.chat.assert_awaited_once()
    async with rag_sessions() as db:
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.operation_key == f"assessment:{job_id}:opportunity:1"))
        assert usage.detail_json["result"]["opportunities"][0]["trigger"]["turn"] == 1
        assert usage.accounted and usage.total_tokens == 15
    plans = build_opportunity_packets(sources, result, through_turn=1, input_budget=10000)
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        original = JudgePacket.model_validate_json(json.dumps(job.input_json["packet"]))
        opportunity = plans.batches[0].model_copy(update={"answer": original.answer})
        packet = original.model_copy(update={"checks": (*original.checks, *opportunity.checks)})
        with pytest.raises(ConversationError, match="固定"):
            await AssessmentStore().begin_run(db, job_id, owner, 1)
        await db.rollback()
        invalid_plans = (
            (packet.model_copy(update={"checks": opportunity.checks}), (opportunity,)),
            (packet.model_copy(update={"sources": (sources[0].model_copy(update={"text": "被改写"}), sources[1])}),
             (original, opportunity)),
            (packet, (packet,)),
        )
        for invalid_packet, invalid_batches in invalid_plans:
            with pytest.raises(ConversationError):
                await PreparationStore().freeze(db, job_id, owner, invalid_packet, invalid_batches)
            await db.rollback()
            unchanged = await db.get(ConversationAssessment, job_id, populate_existing=True)
            assert "preparationFrozen" not in unchanged.input_json
            assert unchanged.input_json["packet"] == original.model_dump(mode="json")
        await PreparationStore().freeze(db, job_id, owner, packet, (original, opportunity))
        frozen = await db.get(ConversationAssessment, job_id, populate_existing=True)
        assert frozen.input_json["preparationFrozen"] is True
        assert frozen.input_json["originalReportInput"]["packet"] == original.model_dump(mode="json")
        with pytest.raises(ConversationError):
            await PreparationStore().begin(db, job_id, owner, 2, sources)
        await db.rollback()
        await AssessmentStore().begin_run(db, job_id, owner, 1)
