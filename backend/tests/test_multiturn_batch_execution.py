"""隔离数据库验证长报告每段调用独立入账及三次完整评审的聚合。"""
import asyncio
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelUsage
from app.models.conversation import ConversationAssessment, ConversationJudgeRun, ConversationUsage
from app.services.multiturn.assessments import AssessmentStore, execute_assessment
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource
from test_multiturn_assessments import FixedJudge, setup_job


class BatchJudge(FixedJudge):
    """按固定检查项返回分数，可让某段无效或取消，其他段正常计费。"""

    def __init__(self, fail_at: int = 0, cancel_at: int = 0) -> None:
        """保存故障发生的实际调用序号。"""
        super().__init__()
        self.fail_at, self.cancel_at = fail_at, cancel_at

    async def chat(self, request):
        """每段输出固定项，低分项不因所在分段不同获得额外权重。"""
        self.calls += 1
        if self.calls == self.cancel_at:
            raise asyncio.CancelledError()
        packet = json.loads(request.messages[-1].content)
        items = [{"id": item["id"], "applicability": "applicable", "rating": 0 if item["id"] in ("g2", "g3") else 4,
                  "passed": None, "reason": "原文检查", "evidence": [{"source_id": "u1", "quote": "原目标"}]}
                 for item in packet["checks"]]
        return ModelReply("invalid" if self.calls == self.fail_at else json.dumps({"items": items}), ModelUsage(10, 5), 1)


async def batch_job(sessions, owner):
    """复用隔离已完成轮次，建立一个包含两段的正式会话作业。"""
    conversation_id, _, _ = await setup_job(sessions, owner, formal=False)
    checks = tuple(JudgeCheck(id=key, dimension=dimension, description="检查机会") for key, dimension in (
        ("g1", "goal"), ("g2", "goal"), ("g3", "goal"), ("m", "memory"),
        ("c", "consistency"), ("r", "correction"), ("e", "efficiency")))
    packet = JudgePacket(scope="session", through_turn=1, answer="截至第一轮的会话报告",
        sources=(JudgeSource(id="u1", turn=1, text="原目标"),), checks=checks)
    batches = (packet.model_copy(update={"checks": checks[:1]}), packet.model_copy(update={"checks": checks[1:]}))
    async with sessions() as db:
        job = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="session:1:1",
            model_config_id=1, packet=packet, batches=batches, formal=True, input_budget=10000, currency="CNY")
        return job.id


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at,valid_runs", [(0, 3), (2, 2)])
async def test_batches_aggregate_items_and_bill_each_call(rag_sessions, rag_users, fail_at, valid_runs) -> None:
    """分段均分不同不能改变机会权重；一段无效只使该次完整评审无效。"""
    job_id = await batch_job(rag_sessions, rag_users[0])
    client = BatchJudge(fail_at=fail_at)
    assert await execute_assessment(rag_sessions, job_id, rag_users[0], client)
    assert client.calls == 6
    assert not await execute_assessment(rag_sessions, job_id, rag_users[0], client)
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        assert job.status == "scored"
        assert job.result_json["valid_runs"] == valid_runs
        assert Decimal(job.result_json["dimensions"]["goal"]) == Decimal("3.33")
        runs = (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id)
                                .order_by(ConversationJudgeRun.run_index))).all()
        assert [(run.result_json["reviewIndex"], run.result_json["batchIndex"]) for run in runs] == [
            (1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)]
        usage = (await db.scalars(select(ConversationUsage).where(ConversationUsage.operation_key.like(f"assessment:{job_id}:run:%")))).all()
        assert len(usage) == 6 and sum(item.total_tokens for item in usage) == 90
        assert all(item.accounted for item in usage)


@pytest.mark.asyncio
async def test_batch_cancellation_preserves_prior_fees_without_automatic_retry(rag_sessions, rag_users) -> None:
    """第四次调用中断，前三次已知费用保留，未发生后续调用不登记。"""
    job_id = await batch_job(rag_sessions, rag_users[0])
    client = BatchJudge(cancel_at=4)
    with pytest.raises(asyncio.CancelledError):
        await execute_assessment(rag_sessions, job_id, rag_users[0], client)
    assert not await execute_assessment(rag_sessions, job_id, rag_users[0], client)
    assert client.calls == 4
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        assert job.status == "interrupted"
        usage = (await db.scalars(select(ConversationUsage).where(ConversationUsage.operation_key.like(f"assessment:{job_id}:run:%")))).all()
        assert len(usage) == 4
        assert sum(item.total_tokens or 0 for item in usage) == 45
        assert sum(item.status == "unknown" and item.total_tokens is None for item in usage) == 1
