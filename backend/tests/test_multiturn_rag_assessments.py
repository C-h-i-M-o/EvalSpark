"""隔离 MySQL 验证 RAG 评分自动提交、证据持久化与正式复评。"""
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.adapters.base import ModelReply, ModelUsage
from app.models.conversation import ConversationAssessment, ConversationJudgeRun
from app.schemas.conversation import TurnCreate
from app.services.multiturn.assessments import AssessmentStore, execute_assessment
from app.services.multiturn.judge import JudgePacket
from app.services.multiturn.rag_judge import RagJudgeEvidence, build_rag_material
from app.services.multiturn.store import ConversationError
from app.services.multiturn.reassessment import submit_reassessment
from test_multiturn_assessments import FixedJudge
from test_multiturn_rag_generation import setup_generator


class EvidenceJudge(FixedJudge):
    """从实际固定输入返回有原文依据的确定性证据判断。"""

    async def chat(self, request):
        """同次返回固定对话项与证据项，模拟三次一致复评。"""
        self.calls += 1
        packet = json.loads(request.messages[-1].content)
        rag = packet["rag"]
        source = rag["evidence"][0]
        return ModelReply(json.dumps({
            "items": [{"id": item["id"], "applicability": "applicable", "rating": 4,
                       "passed": None, "reason": "测试", "evidence": [{"source_id": "answer", "quote": packet["answer"]}]}
                      for item in packet["checks"]],
            "rag": [{"id": item["id"], "needs_citation": True, "support": 0.5,
                     "citation_support": 0.5, "reason": "固定测试部分支持",
                     "evidence": [{"source_id": source["id"], "quote": source["text"]}],
                     "citations": [{"label": "S1", "support": 0.5, "quote": source["text"]}]}
                    for item in rag["segments"]],
        }), ModelUsage(10, 5), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_rag_generation_queues_grounded_assessment_and_formal_snapshot(rag_sessions, rag_users, monkeypatch, historical):
    """生成后固定各分支资料，一次轻评及三次正式评审保存两组分数。"""
    owner = rag_users[0]
    generator, conversation_id, _, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    events = [event async for event in generator.stream(conversation_id, owner,
        TurnCreate(prompt="解释资料", expectedTurn=0, requestKey="rag-score"))]
    if historical:
        events = [event async for event in generator.stream(conversation_id, owner,
            TurnCreate(prompt="解释[T1:S1]", expectedTurn=1, requestKey="rag-history-score"))]
    queued = next(event for event in events if event["type"] == "assessments_queued")
    assert len(queued["assessmentIds"]) == 2
    job_id = queued["assessmentIds"][0]
    client = EvidenceJudge()
    await execute_assessment(rag_sessions, job_id, owner, client)
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        snapshot = job.input_json
        if historical:
            assert snapshot["packet"]["rag"]["evidence"][0]["historical_references"][0]["reference"] == "T1:S1"
        assert job.status == "provisional"
        assert Decimal(job.result_json["evidence"]["final"]) == 5
        assert Decimal(job.result_json["score"]["final"]) == 10
        run = await db.scalar(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id))
        assert run.result_json["ragAssertions"][0]["evidence"]
        assert snapshot["packet"]["rag"]["evidence"][0]["id"].startswith(f"response:{job.response_id}:")
    monkeypatch.setattr("app.services.multiturn.reassessment.publish_assessment", lambda job_id: True)
    async with rag_sessions() as db:
        formal = await submit_reassessment(db, conversation_id, owner, job_id)
        formal_id = formal.id
        assert (await db.get(ConversationAssessment, formal_id)).input_json == snapshot
    await execute_assessment(rag_sessions, formal_id, owner, client)
    assert client.calls == 4
    async with rag_sessions() as db:
        formal = await db.get(ConversationAssessment, formal_id)
        assert formal.status == "scored"
        assert Decimal(formal.result_json["evidence"]["final"]) == 5
        assert formal.result_json["evidence"]["valid_runs"] == 3


@pytest.mark.asyncio
async def test_enqueue_rejects_replaced_branch_evidence(rag_sessions, rag_users, monkeypatch):
    """同一回答不能换用另一个来源标识或不同内容进行评审。"""
    owner = rag_users[0]
    generator, conversation_id, _, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    events = [event async for event in generator.stream(conversation_id, owner,
        TurnCreate(prompt="解释资料", expectedTurn=0, requestKey="rag-forged"))]
    queued = next(event for event in events if event["type"] == "assessments_queued")
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, queued["assessmentIds"][0])
        packet = JudgePacket.model_validate_json(json.dumps(job.input_json["packet"]))
        packet = packet.model_copy(update={"rag": build_rag_material(packet.answer, (
            RagJudgeEvidence(id="response:other:S1", label="S1", text="其他分支资料"),
        ))})
        with pytest.raises(ConversationError) as error:
            await AssessmentStore().enqueue(db, conversation_id, owner, operation_key="forged",
                model_config_id=job.model_config_id, packet=packet, formal=False,
                input_budget=10000, currency="CNY", response_id=job.response_id)
        assert error.value.code == "assessment_evidence_invalid"
