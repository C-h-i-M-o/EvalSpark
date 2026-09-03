from app.models.evaluation import EvaluationResult
from app.models.rag import RagResponseDetail
from app.schemas.evaluation import EvaluationFeedbackRead, EvaluationScoreRead, JudgeRunRead
from app.schemas.rag import RagDetailRead, RagEvidence, RagJudgeRun, RagStageUsage
from app.services.rag.judge import aggregate_rag_runs
from app.services.rag.usage import summarize_usage
from app.services.rule_evaluator import rule_evaluator


def serialize_rag_detail(detail: RagResponseDetail) -> RagDetailRead:
    stages = [RagStageUsage.model_validate(value) for value in detail.stage_usage_json]
    summary = summarize_usage(stages)
    runs = [RagJudgeRun.model_validate(value) for value in detail.judge_runs_json]
    return RagDetailRead(
        knowledge_base_id=detail.knowledge_base_id, knowledge_base_name=detail.knowledge_base_name,
        content_revision=detail.content_revision, embedding_revision=detail.embedding_revision,
        chunk_size=detail.chunk_size, chunk_overlap=detail.chunk_overlap, document_versions=detail.document_versions_json,
        rewritten_query=detail.rewritten_query, evidence=[RagEvidence.model_validate(value) for value in detail.evidence_json],
        stage_usage=stages, external_total_tokens=summary.external_total_tokens,
        cost_by_currency=summary.cost_by_currency, has_unknown_usage=summary.has_unknown_usage,
        judge_runs=runs, judge_aggregate=aggregate_rag_runs(runs) if len(runs) == 3 else None,
        faithfulness=detail.faithfulness, citation_correctness=detail.citation_correctness,
        citation_completeness=detail.citation_completeness, rag_final=detail.rag_final, base_final=detail.base_final,
        score_version=detail.score_version, failure_stage=detail.failure_stage, error_code=detail.error_code,
    )


def serialize_rag_score(result: EvaluationResult | None, detail: RagResponseDetail,
                         prompt: str, answer: str, feedback: EvaluationFeedbackRead) -> EvaluationScoreRead:
    rag = serialize_rag_detail(detail)
    scored = result is not None and result.score_status == "scored" and not result.excluded_from_stats
    aggregate = rag.judge_aggregate
    status = result.score_status if result is not None else "model_failed"
    comments = {"scored": "RAG 三轮联合评审稳定，采用有效轮均值", "judge_failed": "RAG 有效评审轮次不足，本次不计入统计",
                "judge_unstable": "RAG 评审存在维度分歧，本次不计入统计", "model_failed": "RAG 链路失败或尚未完成，本次不计入统计"}
    return EvaluationScoreRead(
        scoreVersion="rag-v1", relevance=float(result.relevance_score) if result else 0,
        completeness=float(result.completeness_score) if result else 0, clarity=float(result.clarity_score) if result else 0,
        format=float(result.format_score) if result else 0, safety=float(result.safety_score) if result else 0,
        ruleFinal=float(result.rule_score) if result else None,
        judgeFinal=float(aggregate.answer_quality) if scored and aggregate and aggregate.answer_quality is not None else None,
        baseFinal=float(detail.base_final) if scored and detail.base_final is not None else None,
        final=float(result.final_score) if scored and result.final_score is not None else None,
        feedbackScore=10 * feedback.like_count / (feedback.like_count + feedback.dislike_count)
            if feedback.like_count + feedback.dislike_count else None,
        details=rule_evaluator.evaluate(prompt=prompt, answer=answer).get("details", {}),
        scoreStatus=status, excludedFromStats=not scored, judgeComment=comments.get(status, comments["model_failed"]),
        judgeRuns=[JudgeRunRead(runIndex=run.run_index, promptCode=run.prompt_code,
            score=float(run.result.answer_quality) if run.result else None, error=run.error_code,
            comment="；".join(claim.reason for claim in run.result.claims) if run.result else None) for run in rag.judge_runs],
        judgeScoreRange=float(max(aggregate.ranges.values())) if aggregate and aggregate.ranges else None,
        ruleDictionaryVersion=result.rule_dictionary_version if result else None,
    )
