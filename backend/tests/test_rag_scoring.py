from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.evaluation import EvaluationResult
from app.models.rag import RagResponseDetail
from app.services.token_quota_service import token_quota_service
from test_rag_evaluation_store import stored
from test_rag_judge import result_payload
from app.services.rag.clients import VectorMatch


@pytest.mark.asyncio
@pytest.mark.parametrize("early_feedback", [False, True])
async def test_persisted_rag_score_history_and_feedback_keep_same_base(stored, monkeypatch, early_feedback) -> None:
    from app.schemas.evaluation import EvaluationFeedbackRead
    from app.services.rag.judge import parse_rag_run
    from app.services.rag.scoring import serialize_rag_score, serialize_rag_detail
    from app.services.rag.usage import external_stage
    from app.adapters.base import ModelReply, ModelUsage
    from test_rag_evaluation import model
    import json
    store, context, prepared, _ = stored
    item = prepared[0]
    item.rewritten_query = "查询"
    item.evidence = await store.read_evidence(context, [VectorMatch("chunk", 1, 2, 0, 0.8)])
    await store.fix_snapshots(context, prepared)
    await store.save_answer(context, item, "需要申请表 [S1]", None)
    async def no_external_bill(*args, **kwargs) -> bool:
        return True
    monkeypatch.setattr(token_quota_service, "record_rag_usage", no_external_bill)
    for index in (1, 2, 3):
        await store.save_stage(context, item.response_id, external_stage("judge", model(1), None, run_index=index, pending=True), start=True)
        run = parse_rag_run(json.dumps(result_payload()), item.evidence, "需要申请表 [S1]", run_index=index)
        await store.save_judge_run(context, item.response_id, run,
            external_stage("judge", model(1), ModelReply("评分", ModelUsage(10, 2), 1), run_index=index))
    # 固定规则分以验算计划中的 7.85 → 8.07。
    from app.services.rule_evaluator import rule_evaluator
    monkeypatch.setattr(rule_evaluator, "evaluate", lambda **kwargs: {
        "relevance": 8, "completeness": 8, "clarity": 8, "format": 8, "safety": 8,
        "final": 8, "ruleFinal": 8, "details": {}, "ruleDictionaryVersion": "test"})
    if early_feedback:
        from app.models.feedback import UserFeedback
        async with store.sessions() as db, db.begin():
            db.add(UserFeedback(response_id=item.response_id, user_id=1, feedback_type="like"))
    await store.finalize_response(context, item.response_id)
    async with store.sessions() as db:
        detail = await db.scalar(select(RagResponseDetail).where(RagResponseDetail.response_id == item.response_id))
        result = await db.scalar(select(EvaluationResult).where(EvaluationResult.response_id == item.response_id))
        assert detail.base_final == Decimal("7.85")
        assert result.final_score == Decimal("8.07" if early_feedback else "7.85")
        read = serialize_rag_detail(detail)
        assert read.judge_aggregate.valid_run_count == 3 and read.external_total_tokens == 36
        score = serialize_rag_score(result, detail, "问题", "回答", EvaluationFeedbackRead(likeCount=1))
        assert score.base_final == 7.85 and score.score_version == "rag-v1"
    from app.services.evaluation_service import EvaluationService
    from app.schemas.evaluation import FeedbackCreate
    evaluator = EvaluationService()
    if early_feedback:
        async with store.sessions() as db:
            removed = await evaluator.toggle_response_feedback(item.response_id, FeedbackCreate(feedbackType="like"), db, 1)
            assert not removed.active and removed.score.final == 7.85
    async with store.sessions() as db:
        toggled = await evaluator.toggle_response_feedback(item.response_id, FeedbackCreate(feedbackType="like"), db, 1)
        assert toggled.score.final == 8.07 and toggled.score.base_final == 7.85
    async with store.sessions() as db:
        task = await evaluator.get_task(context.task_id, db, 1)
        assert task.task_type == "rag" and task.responses[0].score.final == 8.07
        removed = await evaluator.toggle_response_feedback(item.response_id, FeedbackCreate(feedbackType="like"), db, 1)
        assert not removed.active and removed.score.final == 7.85
