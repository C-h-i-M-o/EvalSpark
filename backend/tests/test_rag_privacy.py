import pytest
from sqlalchemy.orm import Session

from app.models.comment import UserComment
from app.models.evaluation import EvaluationTask
from app.models.feedback import UserFeedback
from app.services.evaluation_service import EvaluationService, EvaluationTaskNotFoundError
from app.services.feedback_stats_service import FeedbackStatsService
from test_rag_evaluation_store import stored


@pytest.mark.asyncio
async def test_rag_is_private_even_if_legacy_visibility_was_public(stored) -> None:
    store, context, _, engine = stored
    with Session(engine) as db:
        db.get(EvaluationTask, context.task_id).visibility = "public"
        db.commit()
    async with store.sessions() as db:
        with pytest.raises(EvaluationTaskNotFoundError):
            await EvaluationService().get_task(context.task_id, db, 2)
        listing = await EvaluationService().list_tasks(db, 1, 10, 2, task_type="rag")
        assert listing.total == 0


@pytest.mark.asyncio
async def test_admin_aggregate_has_counts_without_private_bodies_or_activities(stored) -> None:
    store, context, prepared, engine = stored
    with Session(engine) as db:
        db.get(EvaluationTask, context.task_id).prompt = "私有问题不得外泄"
        db.add(UserFeedback(response_id=prepared[0].response_id, user_id=1, feedback_type="like"))
        db.add(UserComment(response_id=prepared[0].response_id, user_id=1, content="私有评论不得外泄"))
        db.commit()
    service = FeedbackStatsService()
    window = service.resolve_range("all")
    async with store.sessions() as db:
        aggregate = await service._load_comments(db, window, aggregate_only=True)
        assert len(aggregate) == 1 and aggregate[0].prompt == "" and aggregate[0].content is None
        assert await service._load_comments(db, window, viewer_id=2) == []
        assert await service._load_feedback(db, window, viewer_id=2) == []
        own = await service._load_comments(db, window, viewer_id=1)
        assert own[0].content == "私有评论不得外泄"
        stats = await service.get_admin_stats(db, "all", "all", None, 1, 20, user_id=2)
        assert stats.activities.total == 0
        assert "私有问题不得外泄" not in stats.model_dump_json()
        assert "私有评论不得外泄" not in stats.model_dump_json()
