"""真实隔离 MySQL 验证评分恢复不重放已开始的调用。"""
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.models.conversation import ConversationAssessment, ConversationJudgeRun, ConversationUsage
from app.services.multiturn.assessments import AssessmentStore, execute_assessment
from app.services.multiturn.recovery import recover_assessments
from test_multiturn_assessments import FixedJudge, setup_job


@pytest.mark.asyncio
async def test_stale_running_is_interrupted_without_republication(rag_sessions, rag_users) -> None:
    """过期执行只标中断，保留未知用量；近期执行不受影响。"""
    owner = rag_users[0]
    _, stale_id, _ = await setup_job(rag_sessions, owner)
    _, active_id, _ = await setup_job(rag_sessions, owner)
    store = AssessmentStore()
    async with rag_sessions() as db:
        await store.claim(db, stale_id, owner)
        await store.begin_run(db, stale_id, owner, 1)
        stale = await db.get(ConversationAssessment, stale_id)
        stale.started_at = datetime.utcnow() - timedelta(hours=5)
        await db.commit()
        await store.claim(db, active_id, owner)
    published: list[int] = []

    def publish(job_id: int) -> bool:
        """记录恢复发出的标识，不启动真实 Worker。"""
        published.append(job_id)
        return True

    await recover_assessments(rag_sessions, publish, limit=100)
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, stale_id)).status == "interrupted"
        assert (await db.get(ConversationAssessment, active_id)).status == "running"
        run = await db.scalar(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == stale_id))
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.operation_key == f"assessment:{stale_id}:run:1"))
        assert run.status == "interrupted"
        assert usage.status == "unknown" and usage.total_tokens is None
        # 清楚结束本测试创建的近期执行，避免后续验收留有假运行状态。
        await store.interrupt(db, active_id, owner)
    assert stale_id not in published and active_id not in published
    client = FixedJudge()
    assert not await execute_assessment(rag_sessions, stale_id, owner, client)
    assert client.calls == 0


@pytest.mark.asyncio
async def test_bounded_publish_failure_preserves_queue(rag_sessions, rag_users) -> None:
    """发送失败不把数据库作业误标成功，下一轮仍能重发。"""
    await setup_job(rag_sessions, rag_users[0])
    async with rag_sessions() as db:
        expected = await db.scalar(select(ConversationAssessment.id)
            .where(ConversationAssessment.status == "queued").order_by(ConversationAssessment.id).limit(1))
    failed = Mock(side_effect=RuntimeError("模拟 broker 不可用"))
    await recover_assessments(rag_sessions, failed, limit=1)
    failed.assert_called_once_with(expected)
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, expected)).status == "queued"
    success = Mock(return_value=True)
    await recover_assessments(rag_sessions, success, limit=1)
    success.assert_called_once_with(expected)


@pytest.mark.asyncio
async def test_recovery_rejects_unbounded_batch() -> None:
    """错误批量参数不能触发数据库扫描。"""
    with pytest.raises(ValueError):
        await recover_assessments(Mock(), Mock(), limit=0)
