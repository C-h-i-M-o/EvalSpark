"""隔离 MySQL 与 ASGI 验证报告权限、固定范围和真实三次执行。"""
import json

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.adapters.base import ModelReply, ModelUsage
from app.db.session import get_db
from app.main import app
from app.models.conversation import ConversationAssessment, ConversationUsage
from app.models.conversation import ConversationJudgeRun
from app.models.user import User
from app.schemas.conversation import TurnCreate
from app.services.multiturn.assessments import execute_assessment
from app.services.multiturn.catalog import conversation_catalog
from app.schemas.conversation import ReportCreate
from app.services.multiturn.reports import submit_report
from app.services.multiturn.report_plan import build_report_plan
from app.services.multiturn.requirements import Requirement
from app.models.conversation import ConversationRequirement, ConversationTurn
from sqlalchemy import select
from test_multiturn_assessments import FixedJudge
from test_multiturn_rag_generation import setup_generator


class ReportJudge(FixedJudge):
    """用实际来源回复固定窗口检查项，不引用报告说明冒充原文。"""

    async def chat(self, request):
        """未知检查项不打分，其余返回可审计的测试判断。"""
        self.calls += 1
        packet = json.loads(request.messages[-1].content)
        if "checks" not in packet:
            user = next(source for source in packet["sources"] if source["id"].endswith(":user"))
            answer = next(source for source in packet["sources"] if source["id"].startswith("response:"))
            return ModelReply(json.dumps({"complete": True, "unresolved": [], "opportunities": [{
                "dimension": "goal", "description": "检查目标完成", "trigger": {"source_id": user["id"], "quote": user["text"]},
                "target": {"source_id": answer["id"], "quote": answer["text"]}, "supporting": []}]}), ModelUsage(10, 5), 1)
        source = packet["sources"][0]
        items = [{"id": check["id"], "applicability": check["required_applicability"] or ("not_applicable" if check.get("coverage_only") else "applicable"),
                  "rating": None if check["required_applicability"] == "unknown" or check.get("coverage_only") else 4,
                  "passed": None, "reason": "测试原文依据",
                  "evidence": [{"source_id": item["id"], "quote": item["text"]} for item in packet["sources"]
                    if item["id"] in (check.get("required_source_ids") or [source["id"]])]}
                 for check in packet["checks"]]
        return ModelReply(json.dumps({"items": items}), ModelUsage(10, 5), 1)


@pytest.mark.asyncio
async def test_report_http_freezes_past_branch_and_disallows_public_writes(rag_sessions, rag_users, monkeypatch) -> None:
    """后续轮次不改变旧报告输入，公开用户能读但不能提交付费报告。"""
    owner = rag_users[0]
    generator, conversation_id, identities, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    _ = [event async for event in generator.stream(conversation_id, owner, TurnCreate(prompt="第一轮目标", expectedTurn=0, requestKey="report-first"))]
    async with rag_sessions() as db:
        await conversation_catalog.set_visibility(db, conversation_id, owner, "public")
    published = []
    monkeypatch.setattr("app.services.multiturn.reports.publish_assessment", lambda identity: published.append(identity))
    actor = [owner]

    async def database():
        """HTTP 数据库仅使用固定隔离测试库。"""
        async with rag_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: User(id=actor[0], username="测试", role="user", status="active")
    path = f"/api/evaluation/conversations/{conversation_id}/reports"
    payload = {"throughTurn": 1, "modelConfigId": identities[0]}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(path, json=payload)
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            async with rag_sessions() as db:
                snapshot = (await db.get(ConversationAssessment, job_id)).input_json
            assert snapshot["packet"]["scope"] == "session" and snapshot["packet"]["through_turn"] == 1
            assert f"branch-{identities[1]}" not in json.dumps(snapshot)
            judge = ReportJudge()
            await execute_assessment(rag_sessions, job_id, owner, judge)
            assert judge.calls == 7  # 一次提取，三组各含覆盖审核与具体机会。
            _ = [event async for event in generator.stream(conversation_id, owner,
                TurnCreate(prompt="第二轮是未来资料", expectedTurn=1, requestKey="report-future"))]
            again = await client.post(path, json=payload)
            assert again.json()["id"] == job_id and again.json()["status"] == "scored"
            assert again.json()["result"]["report"]["turnCount"] == 1
            assert again.json()["result"]["report"]["opportunityReviews"][0]["dimensions"]["goal"]["opportunities"] == 1
            assert again.json()["result"]["report"]["ragEvidenceTrend"][0]["turn"] == 1
            detail = await client.get(f"/api/evaluation/conversations/{conversation_id}/assessments/{job_id}")
            reference = detail.json()["runs"][0]["result"]["items"][0]["evidence_refs"][0]
            assert reference == {"source_id": snapshot["packet"]["sources"][0]["id"],
                                 "turn": 1, "quote": "第一轮目标"}
            async with rag_sessions() as db:
                fixed = (await db.get(ConversationAssessment, job_id)).input_json
                assert fixed["originalReportInput"]["packet"] == snapshot["packet"]
                assert fixed["preparationFrozen"] is True
                usage = (await db.scalars(select(ConversationUsage).where(
                    ConversationUsage.operation_key.like(f"assessment:{job_id}:%")))).all()
                assert len(usage) == 7 and sum(row.total_tokens for row in usage) == 105
                assert all(row.accounted for row in usage)
            assert not await execute_assessment(rag_sessions, job_id, owner, judge)
            assert judge.calls == 7
            listing = await client.get(path)
            assert listing.json()["total"] == 1
            assert [item["id"] for item in listing.json()["items"]] == [job_id]
            assert (await client.post(path, json={**payload, "throughTurn": 99})).status_code == 409
            assert (await client.post(path, json={**payload, "sources": []})).status_code == 422
            actor[0] = rag_users[1]
            assert (await client.post(path, json=payload)).status_code == 404
            assert (await client.get(path)).status_code == 200
            assert published == [job_id]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_report_requirement_scopes_use_retirement_versions_and_complete_turn_sources(rag_sessions, rag_users, monkeypatch) -> None:
    """真实版本记录决定要求窗口；旧预算不检查修改之后的回答。"""
    owner = rag_users[0]
    generator, conversation_id, identities, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    for index, prompt in enumerate(("预算5000", "预算改为7000")):
        _ = [event async for event in generator.stream(conversation_id, owner,
            TurnCreate(prompt=prompt, expectedTurn=index, requestKey=f"scoped-{index}"))]
    async with rag_sessions() as db:
        turns = (await db.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id)
                                  .order_by(ConversationTurn.turn_index))).all()
        old = Requirement(id="budget-old", text="预算5000", quote="预算5000", source_id=f"turn:{turns[0].id}:user",
                          source_turn=1, scope="conversation", critical=True, ambiguous=False)
        new = Requirement(id="budget-new", text="预算7000", quote="预算改为7000", source_id=f"turn:{turns[1].id}:user",
                          source_turn=2, scope="turn", critical=True, ambiguous=False, supersedes=(old.id,))
        # 仅在隔离库创建确定性版本数据，版本保存行为由 requirement_store 专项覆盖。
        for requirement, version, changed_at in ((old, 1, 1), (old.model_copy(update={"retired_at": 2}), 2, 2), (new, 1, 2)):
            db.add(ConversationRequirement(conversation_id=conversation_id, requirement_key=requirement.id,
                version=version, source_turn=changed_at, detail_json=requirement.model_dump(mode="json")))
        await db.commit()
    captured = {}

    def capture_plan(sources, through_turn, budget, checks, *, check_sources):
        """观察真实报告装配边界，同时执行实际预算与分段校验。"""
        captured.update(sources=sources, scopes=check_sources, checks=checks)
        return build_report_plan(sources, through_turn, budget, checks, check_sources=check_sources)

    monkeypatch.setattr("app.services.multiturn.reports.build_report_plan", capture_plan)
    monkeypatch.setattr("app.services.multiturn.reports.publish_assessment", lambda identity: True)
    async with rag_sessions() as db:
        result = await submit_report(db, conversation_id, owner, ReportCreate(modelConfigId=identities[0], throughTurn=2))
    for key, expected_turn in (("requirement:budget-old", 1), ("requirement:budget-new", 2)):
        selected = [source for source in captured["sources"] if source.id in captured["scopes"][key]]
        assert len(selected) == 2 and {source.turn for source in selected} == {expected_turn}
        assert any(source.id.startswith("response:") for source in selected)
        assert any(source.id.endswith(":user") for source in selected)
    assert result.through_turn == 2 and result.status == "queued"


@pytest.mark.asyncio
async def test_invalid_report_discovery_stops_before_formal_runs_without_retry(rag_sessions, rag_users, monkeypatch) -> None:
    """已付费的无效提取保留用量并中断报告，重复投递不会再次付费。"""
    owner = rag_users[0]
    generator, conversation_id, identities, _ = await setup_generator(rag_sessions, owner, monkeypatch)
    _ = [event async for event in generator.stream(conversation_id, owner,
        TurnCreate(prompt="报告提取失败验收", expectedTurn=0, requestKey="report-invalid-discovery"))]
    monkeypatch.setattr("app.services.multiturn.reports.publish_assessment", lambda identity: True)
    async with rag_sessions() as db:
        job = await submit_report(db, conversation_id, owner, ReportCreate(modelConfigId=identities[0], throughTurn=1))
    judge = FixedJudge(malformed=True)
    with pytest.raises(ValueError, match="提取失败"):
        await execute_assessment(rag_sessions, job.id, owner, judge)
    assert not await execute_assessment(rag_sessions, job.id, owner, judge)
    assert judge.calls == 1
    async with rag_sessions() as db:
        assert (await db.get(ConversationAssessment, job.id)).status == "interrupted"
        usage = (await db.scalars(select(ConversationUsage).where(
            ConversationUsage.operation_key.like(f"assessment:{job.id}:%")))).all()
        assert len(usage) == 1 and usage[0].accounted and usage[0].total_tokens == 15
        assert usage[0].detail_json["result"]["status"] == "failed"
        assert not (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job.id))).all()
