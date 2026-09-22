"""隔离库验证状态材料、唯一调用、实际费用和中断后的写入边界。"""
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelUsage
from app.models.conversation import ConversationUsage
from app.models.conversation import ConversationAssessment, ConversationJudgeRun
from app.schemas.multiturn import CheckItem, EvidenceReference
from app.services.multiturn.assessments import AssessmentStore
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeResult, JudgeSource
from app.services.multiturn.issue_resolution import ResolutionRun, parse_resolution
from app.services.multiturn.resolution_store import ResolutionStore
from app.services.multiturn.resolution_store import execute_resolution
from app.services.multiturn.report_resolutions import resolve_report_issues
from app.services.multiturn.store import ConversationError, ConversationStore
from test_multiturn_assessments import FixedJudge, setup_job


async def prepared_job(sessions, owner: int, *, disputed: bool = False):
    """保存真实三组历史失败评审，让状态调用必须从数据库重新推导。"""
    conversation_id, _, _ = await setup_job(sessions, owner)
    sources = (JudgeSource(id="user:1", turn=1, text="预算100"), JudgeSource(id="response:1", turn=1, text="预算不限"),
               JudgeSource(id="response:2", turn=2, text="已改为100"))
    packet = JudgePacket(scope="session", through_turn=2, answer="报告", sources=sources,
        checks=(JudgeCheck(id="issue:budget", dimension="goal", description="是否遵守预算", required_source_ids=("user:1", "response:1")),))
    async with sessions() as db:
        turn, _ = await ConversationStore().reserve_turn(db, conversation_id, owner, prompt="修改预算", expected_turn=1, request_key="resolution")
        await ConversationStore().finish_generation(db, conversation_id, turn.id, owner, status="completed")
        job = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="resolution-report", model_config_id=1,
            packet=packet, formal=True, input_budget=10000, currency="CNY", report_metadata={"throughTurn": 2})
        await AssessmentStore().claim(db, job.id, owner)
        for index in range(1, 4):
            await AssessmentStore().begin_run(db, job.id, owner, index)
            item = CheckItem(id="issue:budget", dimension="goal", rating=4 if disputed and index == 3 else 0,
                reason="预算错误", evidence=["预算不限"], evidence_refs=[EvidenceReference(source_id="response:1", turn=1, quote="预算不限")])
            await AssessmentStore().save_run(db, job.id, owner, index,
                JudgeResult(status="provisional", items=(item,), reply=ModelReply("测试", ModelUsage(10, 5), 1)), FixedJudge())
        return job.id


@pytest.mark.asyncio
async def test_resolution_store_bills_once_and_persists_verified_result(rag_sessions, rag_users) -> None:
    """篡改材料和重复登记被拒绝，有效结论只在已结算后保存且不可覆盖。"""
    owner = rag_users[0]
    job_id = await prepared_job(rag_sessions, owner)
    store = ResolutionStore()
    async with rag_sessions() as db:
        packet = await store.packet(db, job_id, owner, "issue:budget")
        assert packet.sources[-1].id == "response:2" and "预算错误" in packet.description
        with pytest.raises(ConversationError):
            await store.begin(db, job_id, owner, replace(packet, description="伪造问题"), 1)
        await db.rollback()
        await store.begin(db, job_id, owner, packet, 1)
        with pytest.raises(ConversationError, match="重复"):
            await store.begin(db, job_id, owner, packet, 1)
        await db.rollback()
        reply = ModelReply(json.dumps({"issue_id": packet.issue_id, "status": "resolved", "reason": "已修正预算",
            "evidence": [{"source_id": "response:1", "quote": "预算不限"}, {"source_id": "response:2", "quote": "已改为100"}]}), ModelUsage(8, 4), 1)
        run = ResolutionRun(parse_resolution(reply.answer, packet), reply)
        with pytest.raises(ConversationError, match="结算"):
            await store.save_result(db, job_id, owner, packet.issue_id, 1, run)
        await db.rollback()
        await store.save_usage(db, job_id, owner, packet.issue_id, 1, reply, FixedJudge())
        await store.save_usage(db, job_id, owner, packet.issue_id, 1, None, FixedJudge())
        await store.save_result(db, job_id, owner, packet.issue_id, 1, run)
        await store.save_result(db, job_id, owner, packet.issue_id, 1, run)
        usage = await db.scalar(select(ConversationUsage).where(ConversationUsage.operation_key == store._key(job_id, packet.issue_id, 1)))
        assert usage.total_tokens == 12 and usage.accounted and usage.detail_json["result"]["status"] == "resolved"
        with pytest.raises(ConversationError):
            await store.save_result(db, job_id, owner, packet.issue_id, 1, ResolutionRun(error_code="resolution_invalid"))


@pytest.mark.asyncio
async def test_resolution_store_rejects_disagreement_foreign_user_and_late_result(rag_sessions, rag_users) -> None:
    """历史分歧不启动状态请求，中断收尾未知并阻止迟到结论。"""
    owner, reader = rag_users[:2]
    store = ResolutionStore()
    disputed = await prepared_job(rag_sessions, owner, disputed=True)
    async with rag_sessions() as db:
        with pytest.raises(ConversationError, match="一致"):
            await store.packet(db, disputed, owner, "issue:budget")
    job_id = await prepared_job(rag_sessions, owner)
    async with rag_sessions() as db:
        with pytest.raises(ConversationError):
            await store.packet(db, job_id, reader, "issue:budget")
        await db.rollback()
        packet = await store.packet(db, job_id, owner, "issue:budget")
        await store.begin(db, job_id, owner, packet, 1)
        await AssessmentStore().interrupt(db, job_id, owner)
        usage = await db.scalar(select(ConversationUsage).where(ConversationUsage.operation_key == store._key(job_id, packet.issue_id, 1)))
        assert usage.status == "unknown" and usage.total_tokens is None
        with pytest.raises(ConversationError):
            await store.save_result(db, job_id, owner, packet.issue_id, 1, ResolutionRun(error_code="resolution_call_failed"))


@pytest.mark.asyncio
async def test_resolution_execution_persists_invalid_reply_cost_and_never_retries(rag_sessions, rag_users) -> None:
    """实际调用解析链路在无效回复时仍入账，重跑在供应商调用前拒绝。"""
    owner = rag_users[0]
    job_id = await prepared_job(rag_sessions, owner)
    client = FixedJudge()
    client.chat = AsyncMock(return_value=ModelReply("不是JSON", ModelUsage(10, 5), 1))
    result = await execute_resolution(rag_sessions, job_id, owner, "issue:budget", 1, client, output_tokens=2048)
    assert result.error_code == "resolution_invalid"
    with pytest.raises(ConversationError, match="重复"):
        await execute_resolution(rag_sessions, job_id, owner, "issue:budget", 1, client, output_tokens=2048)
    client.chat.assert_awaited_once()
    async with rag_sessions() as db:
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.operation_key == ResolutionStore()._key(job_id, "issue:budget", 1)))
        assert usage.accounted and usage.total_tokens == 15
        assert usage.detail_json["result"]["valid"] is False
        assert usage.detail_json["result"]["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_report_resolution_groups_preserve_historical_score(rag_sessions, rag_users, invalid: bool) -> None:
    """三组状态独立汇总，无效调用仍计费，修正不改写历史失败评分。"""
    owner = rag_users[0]
    job_id = await prepared_job(rag_sessions, owner)
    client = FixedJudge()
    answer = json.dumps({"issue_id": "issue:budget", "status": "resolved", "reason": "预算已修正",
        "evidence": [{"source_id": "response:1", "quote": "预算不限"}, {"source_id": "response:2", "quote": "已改为100"}]})
    client.chat = AsyncMock(return_value=ModelReply("invalid" if invalid else answer, ModelUsage(8, 4), 1))
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        job.input_json = {**job.input_json, "report": {**job.input_json["report"], "resolutionVersion": 1}}
        await db.commit()
        with pytest.raises(ConversationError, match="收尾"):
            await AssessmentStore().finish(db, job_id, owner)
    await resolve_report_issues(rag_sessions, job_id, owner, client, output_tokens=2048)
    await resolve_report_issues(rag_sessions, job_id, owner, client, output_tokens=2048)
    assert client.chat.await_count == 3
    async with rag_sessions() as db:
        await AssessmentStore().finish(db, job_id, owner)
        job = await db.get(ConversationAssessment, job_id, populate_existing=True)
        assert job.result_json["dimensions"]["goal"] == "0.00"
        report = job.result_json["report"]
        assert report["resolutionComplete"] is True
        issue = report["issueResolutions"][0]
        assert issue["status"] == ("unknown" if invalid else "resolved")
        assert len(issue["reviews"]) == 3 and issue["throughTurn"] == 2
        usages = (await db.scalars(select(ConversationUsage).where(ConversationUsage.stage == "resolution",
            ConversationUsage.operation_key.like(f"assessment:{job_id}:%")))).all()
        assert len(usages) == 3 and sum(usage.total_tokens for usage in usages) == 36
        saved = (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id))).all()
        assert len(saved) == 3 and all(run.result_json["items"][0]["rating"] == 0 for run in saved)


@pytest.mark.asyncio
async def test_disputed_historical_failure_is_reported_unknown_without_call(rag_sessions, rag_users) -> None:
    """历史评审有分歧的失败项仍在报告中，但不追加无法确定对象的收费调用。"""
    owner = rag_users[0]
    job_id = await prepared_job(rag_sessions, owner, disputed=True)
    client = FixedJudge()
    client.chat = AsyncMock()
    await resolve_report_issues(rag_sessions, job_id, owner, client, output_tokens=2048)
    client.chat.assert_not_awaited()
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        issue = job.input_json["report"]["issueResolutions"][0]
        assert issue["status"] == "unknown" and issue["errorCode"] == "resolution_failure_disputed"


@pytest.mark.asyncio
async def test_resolution_quota_marks_unexecuted_groups_without_losing_score(rag_sessions, rag_users, monkeypatch) -> None:
    """状态额度不足时保留三组未执行标记，已有历史评审仍可完成保存。"""
    from app.services.token_quota_service import TokenQuotaExceededError
    owner = rag_users[0]
    job_id = await prepared_job(rag_sessions, owner)
    monkeypatch.setattr("app.services.multiturn.resolution_store.token_quota_service.ensure_can_start",
        AsyncMock(side_effect=TokenQuotaExceededError("额度不足")))
    client = FixedJudge()
    client.chat = AsyncMock()
    await resolve_report_issues(rag_sessions, job_id, owner, client, output_tokens=2048)
    client.chat.assert_not_awaited()
    async with rag_sessions() as db:
        await AssessmentStore().finish(db, job_id, owner)
        job = await db.get(ConversationAssessment, job_id, populate_existing=True)
        issue = job.result_json["report"]["issueResolutions"][0]
        assert issue["status"] == "unknown" and len(issue["reviews"]) == 3
        assert all(review["errorCode"] == "resolution_not_run" for review in issue["reviews"])
        assert job.result_json["dimensions"]["goal"] == "0.00"


@pytest.mark.asyncio
async def test_resolution_coordinator_cannot_finish_while_history_is_pending(rag_sessions, rag_users) -> None:
    """历史评审仍运行时不能提前固定空状态列表或发起后续收费调用。"""
    owner = rag_users[0]
    job_id = await prepared_job(rag_sessions, owner)
    async with rag_sessions() as db:
        run = await db.scalar(select(ConversationJudgeRun).where(
            ConversationJudgeRun.assessment_id == job_id, ConversationJudgeRun.run_index == 3))
        run.status = "pending"
        await db.commit()
    client = FixedJudge()
    client.chat = AsyncMock()
    with pytest.raises(ConversationError, match="尚未完整"):
        await resolve_report_issues(rag_sessions, job_id, owner, client, output_tokens=2048)
    client.chat.assert_not_awaited()
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        assert not job.input_json["report"].get("resolutionComplete")
        assert "issueResolutions" not in job.input_json["report"]
