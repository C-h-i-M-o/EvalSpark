"""隔离 MySQL 验证评分任务幂等、权限和逐次用量保存。"""
import asyncio
import json
from decimal import Decimal
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.adapters.base import ModelClient, ModelReply, ModelRequest, ModelUsage
from app.models.conversation import ConversationAssessment, ConversationJudgeRun, ConversationUsage
from app.models.token_usage import DailyUserTokenUsage, UserTokenQuota
from app.services.token_quota_service import token_quota_service, TokenQuotaExceededError
from app.services.multiturn.assessments import AssessmentStore, execute_assessment
from app.services.multiturn.assessment_reader import AssessmentReader
from app.services.multiturn.reassessment import submit_reassessment
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource
from app.services.multiturn.store import ConversationError, ConversationStore


class FixedJudge(ModelClient):
    """返回四维完整评审，记录调用次数以检验幂等。"""

    def __init__(self, malformed: bool = False) -> None:
        """记录是否模拟已付费但无效的返回。"""
        self.calls = 0
        self.malformed = malformed

    async def chat(self, request: ModelRequest) -> ModelReply:
        """模拟已知用量的远程响应。"""
        self.calls += 1
        body = {"items": [{"id": name, "applicability": "applicable", "rating": 4,
            "passed": None, "reason": "符合原文", "evidence": [{"source_id": "answer", "quote": "回答"}]}
            for name in ("solution", "context", "instruction", "expression")]}
        return ModelReply("invalid" if self.malformed else json.dumps(body), ModelUsage(10, 5), 1)

    def get_model_name(self) -> str:
        """返回测试评审配置名称。"""
        return "test-judge"

    def estimate_cost(self, usage: ModelUsage) -> Decimal:
        """测试费用与用量一并保存。"""
        return Decimal("0.01")


async def setup_job(sessions, owner: int, formal: bool = True) -> tuple[int, int, JudgePacket]:
    """建立已完成轮次和固定评分输入，不触碰业务库。"""
    store = ConversationStore()
    packet = JudgePacket(scope="dialogue", through_turn=1, answer="回答",
        sources=(JudgeSource(id="u1", turn=1, text="问题"),),
        checks=tuple(JudgeCheck(id=name, dimension=name, description="检查要求")
                     for name in ("solution", "context", "instruction", "expression")))
    async with sessions() as db:
        conversation = await store.create(db, owner, mode="chat", title="评分作业", visibility="public",
                                           config={"modelIds": [1], "judgeModelId": 2})
        turn, _ = await store.reserve_turn(db, conversation.id, owner, prompt="问题", expected_turn=0, request_key="q")
        await store.finish_generation(db, conversation.id, turn.id, owner, status="completed")
        job = await AssessmentStore().enqueue(db, conversation.id, owner, operation_key="score",
            model_config_id=1, packet=packet, formal=formal, input_budget=10000, currency="CNY")
        return conversation.id, job.id, packet


@pytest.mark.asyncio
async def test_formal_submission_reuses_snapshot_and_concurrent_key(rag_sessions, rag_users, monkeypatch) -> None:
    """并发正式提交只创建一个作业，并完整保留暂定输入和检查项。"""
    owner = rag_users[0]
    conversation_id, source_id, _ = await setup_job(rag_sessions, owner, formal=False)
    await execute_assessment(rag_sessions, source_id, owner, FixedJudge())
    published = []
    monkeypatch.setattr("app.services.multiturn.reassessment.publish_assessment", lambda job_id: published.append(job_id))

    async def submit():
        """独立事务模拟重复点击正式复评。"""
        async with rag_sessions() as db:
            return await submit_reassessment(db, conversation_id, owner, source_id)

    first, second = await asyncio.gather(submit(), submit())
    assert first.id == second.id and first.formal and first.status == "queued"
    assert set(published) == {first.id}
    async with rag_sessions() as db:
        source = await db.get(ConversationAssessment, source_id)
        formal = await db.get(ConversationAssessment, first.id)
        assert formal.input_json == source.input_json
        assert formal.through_turn == source.through_turn
        assert formal.result_json is None and source.status == "provisional"
    judge = FixedJudge()
    await execute_assessment(rag_sessions, first.id, owner, judge)
    assert judge.calls == 3
    assert (await submit()).status == "scored"
    assert judge.calls == 3


@pytest.mark.asyncio
async def test_formal_submission_permissions_pending_and_publish_failure(rag_sessions, rag_users, monkeypatch) -> None:
    """非作者、未完成与跨会话来源被拒绝；投递失败仍保留可恢复作业。"""
    owner, reader = rag_users[:2]
    conversation_id, source_id, _ = await setup_job(rag_sessions, owner, formal=False)
    other_id, _, _ = await setup_job(rag_sessions, owner, formal=False)
    async with rag_sessions() as db:
        for target, actor, status in ((conversation_id, reader, 404), (other_id, owner, 404), (conversation_id, owner, 409)):
            with pytest.raises(ConversationError) as error:
                await submit_reassessment(db, target, actor, source_id)
            assert error.value.status_code == status
    await execute_assessment(rag_sessions, source_id, owner, FixedJudge())

    def offline(job_id):
        """模拟队列断开，不发送真实消息。"""
        raise ConnectionError("test broker offline")

    monkeypatch.setattr("app.services.multiturn.reassessment.publish_assessment", offline)
    async with rag_sessions() as db:
        result = await submit_reassessment(db, conversation_id, owner, source_id)
        assert result.status == "queued"
        with pytest.raises(ConversationError) as recursive:
            await submit_reassessment(db, conversation_id, owner, result.id)
        assert recursive.value.status_code == 409
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, result.id)).status == "queued"


@pytest.mark.asyncio
async def test_assessment_read_current_visibility_and_cross_conversation(rag_sessions, rag_users) -> None:
    """公开评分可读但不泄漏输入，私有后立即拒绝，跨会话作业 ID 无效。"""
    owner, reader = rag_users[:2]
    conversation_id, job_id, _ = await setup_job(rag_sessions, owner)
    other_conversation_id, _, _ = await setup_job(rag_sessions, owner)
    service = AssessmentReader()
    async with rag_sessions() as db:
        pending = await service.get(db, conversation_id, job_id, reader)
        assert pending.status == "queued" and pending.result is None and pending.runs == []
        assert "input_json" not in pending.model_dump() and "operation_key" not in pending.model_dump()
        page = await service.list(db, conversation_id, reader, page_size=1)
        assert page.total == 1 and page.items[0].id == job_id
        assert not (await service.list(db, conversation_id, reader, response_id=999999)).items
        assert not (await service.list(db, conversation_id, reader, page=2, page_size=1)).items
        with pytest.raises(ConversationError) as mismatch:
            await service.get(db, other_conversation_id, job_id, reader)
        assert mismatch.value.status_code == 404
    await execute_assessment(rag_sessions, job_id, owner, FixedJudge())
    async with rag_sessions() as db:
        completed = await service.get(db, conversation_id, job_id, reader)
        assert completed.status == "scored" and Decimal(completed.result["final"]) == 10
        assert [run.run_index for run in completed.runs] == [1, 2, 3]
        from app.models.conversation import Conversation
        conversation = await db.get(Conversation, conversation_id)
        conversation.visibility = "private"
        await db.commit()
    async with rag_sessions() as db:
        for operation in (service.get(db, conversation_id, job_id, reader),
                          service.list(db, conversation_id, reader)):
            with pytest.raises(ConversationError) as forbidden:
                await operation
            assert forbidden.value.status_code == 404
        assert (await service.get(db, conversation_id, job_id, owner)).id == job_id


@pytest.mark.asyncio
async def test_formal_concurrent_delivery_calls_exactly_three_times(rag_sessions, rag_users) -> None:
    """并发投递只有一个执行者，三次结果和费用逐次保存且可重读。"""
    owner = rag_users[0]
    conversation_id, job_id, _ = await setup_job(rag_sessions, owner)
    client = FixedJudge()
    executions = await asyncio.gather(*(execute_assessment(rag_sessions, job_id, owner, client) for _ in range(2)))
    assert sorted(executions) == [False, True]
    assert client.calls == 3
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        assert job.status == "scored"
        assert Decimal(job.result_json["final"]) == 10
        runs = list((await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id))).all())
        usages = list((await db.scalars(select(ConversationUsage).where(ConversationUsage.conversation_id == conversation_id))).all())
        assert len(runs) == len(usages) == 3
        assert sum(row.total_tokens for row in usages) == 45
        assert all(row.accounted for row in usages)
    assert not await execute_assessment(rag_sessions, job_id, owner, client)
    assert client.calls == 3


@pytest.mark.asyncio
async def test_stage_accounting_concurrent_and_utc_date(rag_sessions, rag_users) -> None:
    """两个入账者只能累计一次，跨北京时间午夜按调用开始日期记录。"""
    owner = rag_users[0]
    conversation_id, _, _ = await setup_job(rag_sessions, owner)
    async with rag_sessions() as db:
        usage = ConversationUsage(conversation_id=conversation_id, user_id=owner, operation_key="date-test",
            stage="summary", status="completed", total_tokens=23, currency="CNY", cost=Decimal("0"),
            created_at=datetime(2026, 1, 1, 16, 5), detail_json={}, accounted=False)
        db.add(usage)
        await db.commit()
        usage_id = usage.id

    async def account() -> bool:
        """独立事务模拟两个并发投递的入账者。"""
        async with rag_sessions() as db:
            result = await token_quota_service.record_conversation_usage(db, usage_id=usage_id, user_id=owner)
            await db.commit()
            return result

    assert sorted(await asyncio.gather(account(), account())) == [False, True]
    async with rag_sessions() as db:
        daily = await db.scalar(select(DailyUserTokenUsage).where(
            DailyUserTokenUsage.user_id == owner, DailyUserTokenUsage.usage_date == date(2026, 1, 2)))
        assert daily.total_tokens == 23
        assert (await db.get(ConversationUsage, usage_id)).detail_json["usageDate"] == "2026-01-02"
        with pytest.raises(ValueError, match="无权"):
            await token_quota_service.record_conversation_usage(db, usage_id=usage_id, user_id=rag_users[1])


@pytest.mark.asyncio
async def test_unknown_usage_remains_unaccounted_and_rollback_is_atomic(rag_sessions, rag_users) -> None:
    """未知用量不能伪装零入账，事务回滚同时恢复流水与日累计。"""
    owner = rag_users[0]
    conversation_id, _, _ = await setup_job(rag_sessions, owner)
    async with rag_sessions() as db:
        usage = ConversationUsage(conversation_id=conversation_id, user_id=owner, operation_key="unknown-test",
            stage="judge", status="unknown", currency="CNY", detail_json={}, accounted=False)
        db.add(usage)
        await db.commit()
        usage_id = usage.id
        assert not await token_quota_service.record_conversation_usage(db, usage_id=usage_id, user_id=owner)
        usage.status, usage.total_tokens = "completed", 11
        await db.commit()
        assert await token_quota_service.record_conversation_usage(db, usage_id=usage_id, user_id=owner)
        await db.rollback()
    async with rag_sessions() as db:
        assert not (await db.get(ConversationUsage, usage_id)).accounted
        assert await db.scalar(select(DailyUserTokenUsage).where(DailyUserTokenUsage.user_id == owner)) is None


@pytest.mark.asyncio
async def test_quota_rechecked_between_formal_calls(rag_sessions, rag_users) -> None:
    """首轮用完额度后正式复评不得继续发起第二次收费请求。"""
    owner = rag_users[0]
    _, job_id, _ = await setup_job(rag_sessions, owner)
    async with rag_sessions() as db:
        db.add(UserTokenQuota(user_id=owner, daily_limit=15))
        await db.commit()
    client = FixedJudge()
    with pytest.raises(TokenQuotaExceededError):
        await execute_assessment(rag_sessions, job_id, owner, client)
    assert client.calls == 1
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, job_id)).status == "interrupted"
        daily = await db.scalar(select(DailyUserTokenUsage).where(DailyUserTokenUsage.user_id == owner))
        assert daily.total_tokens == 15


@pytest.mark.asyncio
async def test_invalid_paid_reply_is_recorded(rag_sessions, rag_users) -> None:
    """无效评审不能抹掉实际发生的收费调用。"""
    owner = rag_users[0]
    conversation_id, job_id, _ = await setup_job(rag_sessions, owner, formal=False)
    assert await execute_assessment(rag_sessions, job_id, owner, FixedJudge(malformed=True))
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, job_id)).status == "judge_failed"
        usage = await db.scalar(select(ConversationUsage).where(ConversationUsage.conversation_id == conversation_id))
        assert usage.total_tokens == 15
        assert usage.cost == Decimal("0.01")


@pytest.mark.asyncio
async def test_public_reader_cannot_claim_and_key_cannot_change_input(rag_sessions, rag_users) -> None:
    """公开阅读不授权收费，幂等键不能悄悄替换评分快照。"""
    owner, other = rag_users
    conversation_id, job_id, packet = await setup_job(rag_sessions, owner)
    client = FixedJudge()
    with pytest.raises(ConversationError) as error:
        await execute_assessment(rag_sessions, job_id, other, client)
    assert error.value.status_code == 404 and client.calls == 0
    async with rag_sessions() as db:
        same = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="score",
            model_config_id=1, packet=packet, formal=True, input_budget=10000, currency="CNY")
        assert same.id == job_id
        with pytest.raises(ConversationError) as error:
            await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="score",
                model_config_id=1, packet=packet, formal=False, input_budget=10000, currency="CNY")
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_cancellation_preserves_finished_runs_without_replay(rag_sessions, rag_users) -> None:
    """第二次调用中断后保留第一次结果，重复投递不得重新收费。"""
    class CancellingJudge(FixedJudge):
        """模拟完成一次之后遇到取消的供应商。"""

        async def chat(self, request: ModelRequest) -> ModelReply:
            """第二次请求取消且用量无法确定。"""
            if self.calls == 1:
                raise asyncio.CancelledError
            return await super().chat(request)

    owner = rag_users[0]
    _, job_id, _ = await setup_job(rag_sessions, owner)
    client = CancellingJudge()
    with pytest.raises(asyncio.CancelledError):
        await execute_assessment(rag_sessions, job_id, owner, client)
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, job_id)).status == "interrupted"
        runs = list((await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id))).all())
        assert len(runs) == 2
        assert {run.run_index: run.status for run in runs} == {1: "provisional", 2: "interrupted"}
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.operation_key == f"assessment:{job_id}:run:2"))
        assert usage.status == "unknown" and usage.total_tokens is None
    assert not await execute_assessment(rag_sessions, job_id, owner, client)
