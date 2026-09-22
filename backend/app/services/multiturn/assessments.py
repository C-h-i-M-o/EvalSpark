"""持久化多轮评审作业，模型请求期间不持有数据库事务锁。"""
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ModelClient
from app.models.conversation import ConversationAssessment, ConversationJudgeRun, ConversationTurn, ConversationUsage
from app.schemas.multiturn import CheckItem, Reassessment, RAGAssertion
from app.services.multiturn.judge import JudgePacket, JudgeResult, evaluate_packet
from app.services.multiturn.scoring import SESSION_WEIGHTS, RAG_WEIGHTS, aggregate_reassessments, aggregate_rag_assertion_runs, score_dialogue
from app.services.multiturn.rag_assessment import load_rag_material
from app.services.multiturn.assessment_batches import batch_count, snapshot_batches, validate_batches
from app.services.multiturn.store import ConversationError, ConversationStore
from app.models.user import User
from app.models.response import ModelResponse
from app.services.token_quota_service import token_quota_service
from app.services.rule_evaluator import rule_evaluator


def _json_value(value: object) -> object:
    """将十进制评分转为字符串，确保快照不损失小数精度。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _unavailable_evidence() -> dict[str, object]:
    """原始 RAG 材料超预算未送评时保留未知，不能把空断言当不适用。"""
    return {"status": "incomplete", "final": None, "coverage": "0",
        "dimensions": {name: None for name in RAG_WEIGHTS},
        "applicability": {name: "unknown" for name in RAG_WEIGHTS},
        "errorCode": "rag_evidence_budget_exceeded",
        "reason": "完整回答及全部证据超过输入预算，资料与引用尚未核验。"}


def _report_opportunities(reviews: list[Reassessment], *,
                          coverage_check_ids: frozenset[str] = frozenset()) -> list[dict[str, object]]:
    """逐次记录机会数量与成功次数，不把三次检查叠加成更多验证机会。"""
    result: list[dict[str, object]] = []
    for review in reviews:
        dimensions: dict[str, object] = {}
        if review.valid:
            for dimension in SESSION_WEIGHTS:
                selected = [item for item in review.items if item.dimension == dimension and item.id not in coverage_check_ids]
                coverage = [item for item in review.items if item.dimension == dimension and item.id in coverage_check_ids]
                applicable = [item for item in selected if item.applicability == "applicable"]
                passed = [item for item in applicable if item.rating is not None and item.rating >= 3 and item.passed is not False]
                dimensions[dimension] = {"opportunities": len(applicable), "successful": len(passed),
                    "unknown": sum(item.applicability == "unknown" for item in selected),
                    "notApplicable": sum(item.applicability == "not_applicable" for item in selected),
                    "failedCheckIds": [item.id for item in applicable if item not in passed],
                    "coverageChecked": sum(item.applicability == "not_applicable" for item in coverage),
                    "coverageUnknown": sum(item.applicability == "unknown" for item in coverage)}
        findings = [item.model_dump(mode="json") for item in review.items
                    if item.applicability == "unknown" or (item.applicability == "applicable"
                        and (item.rating is None or item.rating < 3 or item.passed is False))]
        result.append({"reviewIndex": review.run_index, "valid": review.valid,
                       "dimensions": dimensions if review.valid else None,
                       "findings": findings if review.valid else None})
    return result


class AssessmentStore:
    """为内部会话服务提供幂等作业，不接受前端提供的可信评审材料。"""

    async def enqueue(self, db: AsyncSession, conversation_id: int, owner: int, *,
                      operation_key: str, model_config_id: int, packet: JudgePacket,
                      formal: bool, input_budget: int, currency: str,
                      response_id: int | None = None,
                      batches: tuple[JudgePacket, ...] | None = None,
                      report_metadata: dict[str, object] | None = None) -> ConversationAssessment:
        """保存固定输入，只有会话作者可以发起评审。"""
        packet.validate_sources()
        if not operation_key or len(operation_key) > 64 or currency not in ("CNY", "USD"):
            raise ConversationError("assessment_invalid", "评分请求键或币种无效", 422)
        if type(input_budget) is not int or input_budget <= 0 or type(formal) is not bool:
            raise ConversationError("assessment_invalid", "评分预算或模式无效", 422)
        if batches is not None:
            if not formal and packet.scope != "dialogue":
                raise ConversationError("assessment_invalid", "非正式分段只允许单轮评审", 422)
            validate_batches(packet, batches, input_budget)
        if report_metadata is not None and (packet.scope != "session" or not formal):
            raise ConversationError("assessment_invalid", "报告元数据只能随正式会话报告保存", 422)
        try:
            conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True, lock=True)
            if model_config_id not in (conversation.config_json or {}).get("modelIds", []):
                raise ConversationError("assessment_invalid_model", "评分分支不属于会话", 422)
            turn = await db.scalar(select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.turn_index == packet.through_turn))
            allowed_states = ("completed", "interrupted") if packet.scope == "session" else ("completed",)
            if turn is None or turn.generation_status not in allowed_states:
                raise ConversationError("assessment_turn_pending", "请等待目标轮次生成完成", 409)
            if response_id is not None:
                response = await db.scalar(select(ModelResponse).where(ModelResponse.id == response_id,
                    ModelResponse.task_id == turn.task_id, ModelResponse.model_config_id == model_config_id,
                    ModelResponse.status == "success"))
                if response is None or packet.scope != "dialogue":
                    raise ConversationError("assessment_response_invalid", "评分回答不属于本轮模型分支", 422)
                if conversation.mode == "rag":
                    material = await load_rag_material(db, response_id, response.answer_text)
                    if packet.rag != material or packet.answer != material.answer:
                        raise ConversationError("assessment_evidence_invalid", "评分资料与实际回答快照不符", 422)
                elif packet.rag is not None or packet.answer not in (
                        response.answer_text, rule_evaluator._strip_think_content(response.answer_text).strip()):
                    raise ConversationError("assessment_response_invalid", "评分回答与原文不符", 422)
            elif packet.rag is not None or (conversation.mode == "rag" and packet.scope == "dialogue"):
                raise ConversationError("assessment_response_invalid", "RAG 单轮评分必须关联成功回答", 422)
            snapshot = {"packet": packet.model_dump(mode="json"), "inputBudget": input_budget,
                        "currency": currency}
            if batches is not None:
                snapshot["batches"] = [batch.model_dump(mode="json") for batch in batches]
            if report_metadata is not None:
                snapshot["report"] = report_metadata
            existing = await db.scalar(select(ConversationAssessment).where(
                ConversationAssessment.conversation_id == conversation_id,
                ConversationAssessment.operation_key == operation_key))
            if existing is not None:
                if (existing.input_json != snapshot or existing.formal != formal
                        or existing.model_config_id != model_config_id or existing.response_id != response_id):
                    raise ConversationError("assessment_key_conflict", "请求键已用于其他评分输入", 409)
                await db.commit()
                return existing
            job = ConversationAssessment(conversation_id=conversation_id, model_config_id=model_config_id,
                through_turn=packet.through_turn, operation_key=operation_key,
                score_version=f"{conversation.mode}-multiturn-v1", formal=formal, input_json=snapshot,
                status="queued", response_id=response_id)
            db.add(job)
            await db.commit()
            return job
        except Exception:
            await db.rollback()
            raise

    async def claim(self, db: AsyncSession, job_id: int, owner: int) -> ConversationAssessment | None:
        """原子认领一次作业，重复投递不重跑已经开始的收费请求。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                              .with_for_update().execution_options(populate_existing=True))
        if job is None:
            await db.rollback()
            raise ConversationError("assessment_not_found", "评分作业不存在", 404)
        try:
            await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
            if job.status != "queued":
                await db.commit()
                return None
            job.status = "running"
            job.started_at = datetime.utcnow()
            await db.commit()
            return job
        except Exception:
            await db.rollback()
            raise

    async def begin_run(self, db: AsyncSession, job_id: int, owner: int, index: int) -> None:
        """收费调用前保存待定流水，中断时可明确识别未知用量。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                              .with_for_update().execution_options(populate_existing=True))
        if job is None:
            raise ConversationError("assessment_not_found", "评分作业不存在", 404)
        await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
        if job.status != "running" or index not in range(1, (3 if job.formal else 1) * batch_count(job.input_json) + 1):
            raise ConversationError("assessment_state_conflict", "评分状态或次数已变更", 409)
        preparation = (await db.scalars(select(ConversationUsage).where(
            ConversationUsage.conversation_id == job.conversation_id, ConversationUsage.user_id == owner,
            ConversationUsage.stage == "opportunity",
            ConversationUsage.operation_key.like(f"assessment:{job.id}:opportunity:%")))).all()
        if any(usage.status == "pending" or "result" not in usage.detail_json for usage in preparation):
            raise ConversationError("preparation_state_conflict", "报告准备结果尚未保存，不能启动正式评审", 409)
        if preparation and not job.input_json.get("preparationFrozen"):
            raise ConversationError("preparation_state_conflict", "准备结果尚未固定到正式评分输入", 409)
        user = await db.get(User, owner, populate_existing=True)
        if user is None or user.status != "active":
            raise ConversationError("assessment_user_disabled", "用户不存在或已停用", 403)
        await token_quota_service.ensure_can_start(db, user)
        db.add(ConversationJudgeRun(assessment_id=job_id, run_index=index, status="pending"))
        db.add(ConversationUsage(conversation_id=job.conversation_id, user_id=owner,
            operation_key=f"assessment:{job.id}:run:{index}", stage="judge", status="pending",
            currency=job.input_json["currency"], detail_json={"assessmentId": job.id, "runIndex": index},
            accounted=False))
        await db.commit()

    async def save_run(self, db: AsyncSession, job_id: int, owner: int, index: int,
                       result: JudgeResult, client: ModelClient) -> None:
        """每次返回后先持久化判定与用量，避免下一次失败抹掉已完成评审。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                              .with_for_update().execution_options(populate_existing=True))
        if job is None:
            raise ConversationError("assessment_not_found", "评分作业不存在", 404)
        await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
        if job.status != "running" or index not in range(1, (3 if job.formal else 1) * batch_count(job.input_json) + 1):
            raise ConversationError("assessment_state_conflict", "评分状态或次数已变更", 409)
        existing = await db.scalar(select(ConversationJudgeRun).where(
            ConversationJudgeRun.assessment_id == job_id, ConversationJudgeRun.run_index == index))
        if existing is not None and existing.status != "pending":
            await db.commit()
            return
        if existing is None:
            raise ConversationError("assessment_state_conflict", "评分调用尚未登记", 409)
        existing.status = result.status
        existing.error_code = result.error_code
        existing.result_json = {"items": [item.model_dump(mode="json") for item in result.items],
                               "score": _json_value(asdict(result.score)) if result.score else None}
        if "batches" in job.input_json:
            size = batch_count(job.input_json)
            existing.result_json = {**existing.result_json,
                "reviewIndex": (index - 1) // size + 1, "batchIndex": (index - 1) % size + 1}
        if job.input_json["packet"].get("rag") is not None:
            existing.result_json = {**existing.result_json,
                "ragAssertions": [item.model_dump(mode="json") for item in result.rag_assertions],
                "evidence": _json_value(asdict(result.rag_score)) if result.rag_score else None}
        reply = result.reply
        known = reply is not None and reply.usage_known
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.conversation_id == job.conversation_id,
            ConversationUsage.operation_key == f"assessment:{job.id}:run:{index}"))
        if usage is None:
            raise ConversationError("assessment_state_conflict", "评分用量尚未登记", 409)
        usage.status = "completed" if known else "unknown"
        usage.total_tokens = reply.usage.total_tokens if known else None
        usage.cost = client.estimate_cost(reply.usage) if known else None
        usage.detail_json = {"assessmentId": job.id, "runIndex": index,
                             "usage": asdict(reply.usage) if known else None}
        await db.flush()
        await token_quota_service.record_conversation_usage(db, usage_id=usage.id, user_id=owner)
        await db.commit()

    async def finish(self, db: AsyncSession, job_id: int, owner: int) -> None:
        """所有规定调用均落库后聚合成绩，中断状态不能被迟到结果覆盖。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                              .with_for_update().execution_options(populate_existing=True))
        if job is None:
            raise ConversationError("assessment_not_found", "评分作业不存在", 404)
        await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
        size = batch_count(job.input_json)
        count = (3 if job.formal else 1) * size
        batches = snapshot_batches(job.input_json)
        rag_parts = {index for index, batch in enumerate(batches, 1) if batch.rag is not None}
        saved = list((await db.scalars(select(ConversationJudgeRun).where(
            ConversationJudgeRun.assessment_id == job_id))).all())
        if (job.status != "running" or {run.run_index for run in saved} != set(range(1, count + 1))
                or any(run.status == "pending" for run in saved)):
            raise ConversationError("assessment_state_conflict", "评分尚未完整保存或已中断", 409)
        if job.input_json.get("report", {}).get("resolutionVersion") == 1 and not job.input_json["report"].get("resolutionComplete"):
            raise ConversationError("resolution_state_conflict", "问题状态流程尚未收尾", 409)
        if job.formal:
            # 每次正式评审必须覆盖全部分段，不能把各段当作独立复评或平均段分。
            reviews: list[Reassessment] = []
            for review_index in range(1, 4):
                group = sorted((run for run in saved if (run.run_index - 1) // size + 1 == review_index),
                               key=lambda run: run.run_index)
                reviews.append(Reassessment(valid=all(run.status == "provisional" for run in group),
                    run_index=review_index, items=[CheckItem.model_validate(item)
                        for run in group for item in (run.result_json or {}).get("items", [])]))
            score = aggregate_reassessments(reviews,
                session=job.input_json["packet"]["scope"] == "session")
            job.status = score.status
            job.result_json = _json_value(asdict(score))
            if "batches" in job.input_json:
                job.result_json = {**job.result_json, "batchCount": size}
            if job.input_json["packet"].get("rag") is not None:
                packet = JudgePacket.model_validate_json(json.dumps(job.input_json["packet"]))
                # 每组只聚合携带完整资料的那一段，其他质量分段不当成额外证据复评。
                evidence_runs = []
                for review in range(3):
                    selected = [run for run in saved if review * size < run.run_index <= (review + 1) * size
                        and (run.run_index - 1) % size + 1 in rag_parts]
                    evidence_runs.append([RAGAssertion.model_validate_json(json.dumps(item))
                        for run in selected for item in (run.result_json or {}).get("ragAssertions", [])]
                        if selected and all(run.status == "provisional" for run in selected) else None)
                evidence_score = aggregate_rag_assertion_runs(evidence_runs, packet.answer,
                    {item.label for item in packet.rag.evidence}) if rag_parts else None
                evidence_status = evidence_score.status if evidence_score is not None else "incomplete"
                job.status = next((state for state in ("judge_failed", "judge_unstable", "incomplete")
                                   if state in (score.status, evidence_status)), "scored")
                job.result_json = {"dialogue": _json_value(asdict(score)),
                    "evidence": _json_value(asdict(evidence_score)) if evidence_score is not None else _unavailable_evidence()}
        else:
            # 单轮分段的检查项各自只出现一次，合并后再统一计算，避免按段平均或重复计分。
            items = [CheckItem.model_validate(item) for run in sorted(saved, key=lambda item: item.run_index)
                     for item in (run.result_json or {}).get("items", [])]
            valid = all(run.status == "provisional" for run in saved)
            score = score_dialogue(items) if valid else None
            job.status = "provisional" if valid else next((run.status for run in saved if run.status != "provisional"), "judge_failed")
            job.result_json = {"score": _json_value(asdict(score)) if score else None,
                               "errorCode": None if valid else next((run.error_code for run in saved if run.error_code), "judge_failed")}
            if job.input_json["packet"].get("rag") is not None:
                evidence_run = next((run for run in saved if run.run_index in rag_parts), None)
                job.result_json = {**job.result_json, "evidence": (evidence_run.result_json or {}).get("evidence")
                    if evidence_run is not None else _unavailable_evidence()}
                if evidence_run is None and valid:
                    job.status = "incomplete"
        if "batches" in job.input_json:
            job.result_json = {**job.result_json, "batchCount": size}
        job.completed_at = datetime.utcnow()
        if "report" in job.input_json:
            coverage_ids = frozenset(check["id"] for check in job.input_json["packet"]["checks"]
                if check.get("coverage_only") or check["id"].startswith("discovery:"))
            job.result_json = {**job.result_json, "report": {**job.input_json["report"],
                "opportunityReviews": _report_opportunities(reviews, coverage_check_ids=coverage_ids)}}
        await db.commit()

    async def interrupt(self, db: AsyncSession, job_id: int, owner: int) -> None:
        """中断只结束当前作业，保留既有调用及费用，禁止自动重新付费。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
                              .with_for_update().execution_options(populate_existing=True))
        if job is not None:
            await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
            if job.status == "running":
                preparation = (await db.scalars(select(ConversationUsage).where(
                    ConversationUsage.conversation_id == job.conversation_id, ConversationUsage.user_id == owner,
                    ConversationUsage.stage.in_(("opportunity", "resolution")), ConversationUsage.status == "pending",
                    ConversationUsage.operation_key.like(f"assessment:{job.id}:%"))
                    .with_for_update())).all()
                for usage in preparation:
                    usage.status = "unknown"
                    usage.detail_json = {**usage.detail_json, "errorCode": "preparation_interrupted"}
                pending = list((await db.scalars(select(ConversationJudgeRun).where(
                    ConversationJudgeRun.assessment_id == job.id, ConversationJudgeRun.status == "pending"))).all())
                for run in pending:
                    run.status = "interrupted"
                    run.error_code = "judge_interrupted"
                    usage = await db.scalar(select(ConversationUsage).where(
                        ConversationUsage.conversation_id == job.conversation_id,
                        ConversationUsage.operation_key == f"assessment:{job.id}:run:{run.run_index}"))
                    if usage is not None:
                        usage.status = "unknown"
                job.status = "interrupted"
                job.completed_at = datetime.utcnow()
        await db.commit()


async def execute_assessment(sessions: async_sessionmaker[AsyncSession], job_id: int,
                             owner: int, client: ModelClient, *, max_output_tokens: int = 4096) -> bool:
    """认领并执行持久化评分；调用方负责评审配置、权限快照与额度预检。"""
    store = AssessmentStore()
    async with sessions() as db:
        job = await store.claim(db, job_id, owner)
        if job is None:
            return False
        snapshot, formal = job.input_json, job.formal
    try:
        if snapshot.get("report", {}).get("semanticPreparation") in (1, 2, 3) and not snapshot.get("preparationFrozen"):
            from app.services.multiturn.semantic_report import prepare_report
            snapshot = await prepare_report(sessions, job_id, owner, client, snapshot, max_output_tokens)
        batches = snapshot_batches(snapshot)
        for review in range(3 if formal else 1):
            for part, packet in enumerate(batches, 1):
                index = review * len(batches) + part
                async with sessions() as db:
                    await store.begin_run(db, job_id, owner, index)
                result = await evaluate_packet(client, packet, input_budget=snapshot["inputBudget"],
                                               max_output_tokens=max_output_tokens)
                async with sessions() as db:
                    await store.save_run(db, job_id, owner, index, result, client)
        if snapshot.get("report", {}).get("resolutionVersion") == 1:
            from app.services.multiturn.report_resolutions import resolve_report_issues
            await resolve_report_issues(sessions, job_id, owner, client, output_tokens=max_output_tokens)
        async with sessions() as db:
            await store.finish(db, job_id, owner)
        return True
    except (Exception, asyncio.CancelledError):
        async with sessions() as db:
            await store.interrupt(db, job_id, owner)
        raise
