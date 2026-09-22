"""真实专用 Redis 队列、Celery Worker 和确定性 HTTP Judge 验收。"""
import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.api_keys import store_api_key
from app.core.config import settings
from app.models.conversation import Conversation, ConversationAssessment, ConversationJudgeRun, ConversationTurn, ConversationUsage
from app.models.model_config import ModelConfig, ModelProvider
from app.schemas.conversation import ConversationCreate
from app.services.multiturn.assessments import AssessmentStore
from app.services.multiturn.catalog import conversation_catalog
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource
from app.services.multiturn.store import ConversationStore
from app.services.multiturn.report_plan import build_report_plan
from app.worker import celery_app

pytestmark = pytest.mark.skipif(os.environ.get("MULTITURN_QUEUE_TESTS") != "1", reason="需要显式启用专用真实队列验收")


@pytest.fixture(autouse=True)
def isolated_queue() -> None:
    """写入前验证数据库和 Redis 端点，禁止消费历史或业务队列。"""
    database = make_url(settings.database_url)
    assert database.host == "mysql-test" and database.database == "multichateval_rag_test"
    assert settings.rag_redis_url.host == "redis-test"
    assert os.environ.get("RAG_INTEGRATION_TESTS") == "1"
    assert os.environ.get("MULTITURN_TEST_QUEUE", "").startswith("multiturn-verification-")


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic", [False, True])
async def test_real_worker_formal_score_duplicate_delivery_and_recovery_thread(rag_sessions, rag_users, semantic: bool) -> None:
    """三次 HTTP 评审落库计费，重复消息无额外调用，生产恢复线程释放失活轮次。"""
    owner = rag_users[0]
    marker = f"queue-proof-{uuid4().hex}"
    queue = os.environ["MULTITURN_TEST_QUEUE"]
    async with rag_sessions() as db:
        provider = ModelProvider(name=marker, base_url="http://model-test:8080/v1", api_key_encrypted=store_api_key("rag-acceptance-only"))
        db.add(provider)
        await db.flush()
        candidate = ModelConfig(provider_id=provider.id, model_name="candidate-a", display_name="队列候选")
        judge = ModelConfig(provider_id=provider.id, model_name="multiturn-judge", display_name="队列评审",
            price_input=Decimal("1"), price_output=Decimal("2"), currency="CNY", max_tokens=2048, timeout_seconds=30)
        db.add_all([candidate, judge])
        await db.commit()
        conversation = await conversation_catalog.create(db, owner, ConversationCreate(mode="chat", title=marker,
            modelIds=[candidate.id], judgeModelId=judge.id, inputBudget=10000))
        identity = conversation.id
        turn, _ = await ConversationStore().reserve_turn(db, identity, owner, prompt="固定队列目标", expected_turn=0, request_key=marker)
        await ConversationStore().finish_generation(db, identity, turn.id, owner, status="completed")
        packet = JudgePacket(scope="session", through_turn=1, answer=marker,
            sources=(JudgeSource(id=f"turn:{turn.id}:user", turn=1, text="固定队列目标"),),
            checks=tuple(JudgeCheck(id=dimension, dimension=dimension, description="确定性管线验收")
                         for dimension in ("goal", "memory", "consistency", "correction", "efficiency")))
        batches = None
        metadata = None
        if semantic:
            plan = build_report_plan((JudgeSource(id=f"turn:{turn.id}:user", turn=1, text=marker),
                JudgeSource(id="response:queue-test", turn=1, text="确定性候选回答")), 1, 10000)
            packet = plan.packet.model_copy(update={"answer": marker})
            batches = tuple(batch.model_copy(update={"answer": marker}) for batch in plan.batches)
            metadata = {"semanticPreparation": 1, "limitations": []}
        job = await AssessmentStore().enqueue(db, identity, owner, operation_key=marker, model_config_id=candidate.id,
            packet=packet, batches=batches, report_metadata=metadata, formal=True, input_budget=10000, currency="CNY")
        job_id = job.id
    # 真正发往本次 Worker 消费的队列，消息只包含服务端作业标识。
    await asyncio.to_thread(celery_app.send_task, "app.worker.run_conversation_assessment", args=[job_id], queue=queue, retry=False)
    for _ in range(45):
        async with rag_sessions() as db:
            job = await db.get(ConversationAssessment, job_id)
            status = job.status
        if status not in ("queued", "running"):
            break
        await asyncio.sleep(1)
    assert status == "scored", status
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        assert job.result_json["final"] == "10.00" and job.result_json["valid_runs"] == 3
        runs = (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id))).all()
        usages = (await db.scalars(select(ConversationUsage).where(ConversationUsage.operation_key.like(f"assessment:{job_id}:%")))).all()
        calls = 7 if semantic else 3
        assert len(runs) == (6 if semantic else 3) and len(usages) == calls
        assert sum(item.total_tokens for item in usages) == calls * 24 and all(item.accounted for item in usages)
        assert sum(item.cost for item in usages) == Decimal("0.000028") * calls
        if semantic:
            assert job.input_json["preparationFrozen"]
            assert job.result_json["report"]["semanticOpportunityCount"] == 1
        stale, _ = await ConversationStore().reserve_turn(db, identity, owner, prompt="失活恢复目标", expected_turn=1, request_key=f"stale-{marker}")
        stale_id = stale.id
        conversation = await db.get(Conversation, identity)
        conversation.updated_at = datetime.utcnow() - timedelta(hours=5)
        await db.commit()
    await asyncio.to_thread(celery_app.send_task, "app.worker.run_conversation_assessment", args=[job_id], queue=queue, retry=False)
    # 不直接调用恢复函数，等待生产 bootstep 的下一次真实扫描。
    for _ in range(90):
        async with rag_sessions() as db:
            turn = await db.get(ConversationTurn, stale_id)
            state = turn.generation_status
        if state == "interrupted":
            break
        await asyncio.sleep(1)
    assert state == "interrupted"
    async with rag_sessions() as db:
        assert (await db.get(Conversation, identity)).generation_status == "idle"
        assert (await db.get(ConversationTurn, stale_id)).error_code == "generation_heartbeat_expired"
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(f"http://model-test:8080/test/multiturn-calls/{marker}")
        assert response.status_code == 200 and response.json()["calls"] == calls
