"""固定截止轮次原文并提交正式会话报告，不复用单轮总分作为会话质量。"""
import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationAssessment, ConversationTurn
from app.models.response import ModelResponse
from app.schemas.conversation import AssessmentRead, ReportCreate
from app.services.multiturn.assessment_reader import project_assessment
from app.services.multiturn.assessments import AssessmentStore
from app.services.multiturn.dispatch import publish_assessment, resolve_judge
from app.services.multiturn.judge import JudgeCheck, JudgeSource
from app.services.multiturn.report_plan import build_report_plan
from app.services.multiturn.requirement_store import load_requirements
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.rule_evaluator import rule_evaluator

logger = logging.getLogger(__name__)


async def submit_report(db: AsyncSession, conversation_id: int, owner: int, payload: ReportCreate) -> AssessmentRead:
    """作者固定一份分支报告，重复请求复用原作业，后续对话不改变过去报告。"""
    try:
        conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True, lock=True)
        config = conversation.config_json or {}
        if payload.model_config_id not in config.get("modelIds", []):
            raise ConversationError("report_invalid_model", "报告模型不属于会话", 422)
        if payload.through_turn > conversation.current_turn:
            raise ConversationError("report_invalid_turn", "报告截止轮次尚不存在", 409)
        operation = f"report:{payload.model_config_id}:{payload.through_turn}"
        existing = await db.scalar(select(ConversationAssessment).where(
            ConversationAssessment.conversation_id == conversation_id, ConversationAssessment.operation_key == operation))
        if existing is not None:
            result = project_assessment(existing)
            await db.commit()
        else:
            turns = (await db.scalars(select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation_id, ConversationTurn.turn_index <= payload.through_turn)
                .order_by(ConversationTurn.turn_index))).all()
            if (not turns or len(turns) != payload.through_turn
                    or any(turn.generation_status not in ("completed", "interrupted") for turn in turns)):
                raise ConversationError("report_turn_pending", "请先结束报告范围内的生成轮次", 409)
            responses = (await db.scalars(select(ModelResponse).where(ModelResponse.task_id.in_([turn.task_id for turn in turns]),
                ModelResponse.model_config_id == payload.model_config_id).order_by(ModelResponse.id))).all()
            by_task: dict[int, ModelResponse] = {}
            for response in responses:
                previous = by_task.get(response.task_id)
                if previous is not None and previous.status == "success" and response.status == "success":
                    raise ConversationError("report_branch_conflict", "报告分支存在重复回答，无法确定原文", 409)
                if previous is None or previous.status != "success":
                    by_task[response.task_id] = response
            sources: list[JudgeSource] = []
            failed: list[int] = []
            for turn in turns:
                sources.append(JudgeSource(id=f"turn:{turn.id}:user", turn=turn.turn_index, text=turn.prompt))
                response = by_task.get(turn.task_id)
                answer = rule_evaluator._strip_think_content(response.answer_text).strip() if response is not None else ""
                if response is not None and response.status == "success" and answer:
                    sources.append(JudgeSource(id=f"response:{response.id}", turn=turn.turn_index, text=answer))
                else:
                    failed.append(turn.turn_index)
            judge = await resolve_judge(db, conversation)
            if judge is None:
                raise ConversationError("report_judge_missing", "会话未配置独立评审模型", 422)
            budget = int(config.get("inputBudget", 8192))
            try:
                missing = (JudgeCheck(id="generation:missing", dimension="goal", required_applicability="unknown",
                    description="报告范围内存在未成功的回答，不能声称全部目标已经完成。"),) if failed else ()
                requirements = await load_requirements(db, conversation_id, owner, through_turn=payload.through_turn)
                requirement_checks = []
                check_sources: dict[str, tuple[str, ...]] = {}
                for requirement in requirements:
                    end = requirement.source_turn if requirement.scope == "turn" else min(
                        payload.through_turn, (requirement.retired_at - 1) if requirement.retired_at else payload.through_turn)
                    requirement_checks.append(JudgeCheck(id=f"requirement:{requirement.id}", dimension="goal",
                        requirement_id=requirement.id, critical=requirement.critical,
                        ambiguous_source_id=requirement.source_id if requirement.ambiguous else None,
                        description=f"仅检查第{requirement.source_turn}至{end}轮期间的要求：{requirement.text}；后续修改不追溯影响。"
                            + ("先依据本分支历史解析用户指代，无法唯一确定时保持unknown。" if requirement.ambiguous else "")))
                    check_sources[f"requirement:{requirement.id}"] = tuple(source.id for source in sources
                        if (1 if requirement.ambiguous else requirement.source_turn) <= source.turn <= end)
                plan = build_report_plan(tuple(sources), payload.through_turn, budget, (*missing, *requirement_checks),
                                         check_sources=check_sources, pair_ready=True)
            except ValueError as error:
                raise ConversationError("report_budget_invalid", str(error), 422) from error
            metadata = {"throughTurn": payload.through_turn, "turnCount": len(turns),
                "semanticPreparation": 3,
                "resolutionVersion": 1,
                "successfulResponses": len(turns) - len(failed), "failedGenerationTurns": failed,
                "sourceCount": len(sources), "sourceCoverage": "1", "limitations": list(plan.limitations)}
            if conversation.mode == "rag":
                scores = (await db.scalars(select(ConversationAssessment).where(
                    ConversationAssessment.conversation_id == conversation_id,
                    ConversationAssessment.model_config_id == payload.model_config_id,
                    ConversationAssessment.through_turn <= payload.through_turn,
                    ConversationAssessment.response_id.is_not(None)).order_by(ConversationAssessment.id.desc()))).all()
                latest: dict[int, ConversationAssessment] = {}
                for score in scores:
                    latest.setdefault(score.through_turn, score)
                metadata["ragEvidenceTrend"] = [{"turn": turn.turn_index,
                    "assessmentId": latest[turn.turn_index].id if turn.turn_index in latest else None,
                    "status": latest[turn.turn_index].status if turn.turn_index in latest else "not_assessed",
                    "evidence": (latest[turn.turn_index].result_json or {}).get("evidence") if turn.turn_index in latest else None,
                } for turn in turns]
            job = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key=operation,
                model_config_id=payload.model_config_id, packet=plan.packet, batches=plan.batches,
                formal=True, input_budget=budget, currency=judge.currency, report_metadata=metadata)
            result = project_assessment(job)
    except Exception:
        await db.rollback()
        raise
    if result.status == "queued":
        try:
            await run_in_threadpool(publish_assessment, result.id)
        except Exception:
            logger.warning("报告投递失败，作业已保存等待恢复扫描")
    return result
