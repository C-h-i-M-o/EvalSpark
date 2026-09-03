from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import UserComment
from app.models.evaluation import EvaluationResult, EvaluationTask
from app.models.feedback import UserFeedback
from app.models.model_config import ModelConfig
from app.models.response import ModelResponse
from app.models.user import User
from app.schemas.feedback_stats import (
    AdminFeedbackStatsRead,
    FeedbackActivityListRead,
    FeedbackActivityRead,
    FeedbackInteractionSummary,
    FeedbackModelStats,
    FeedbackStatsRange,
    FeedbackStatsSummary,
    FeedbackTrendPoint,
    PersonalFeedbackStatsRead,
)

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class StatsRangeWindow:
    name: FeedbackStatsRange
    start_at: datetime | None
    end_at: datetime
    start_at_utc: datetime | None
    end_at_utc: datetime


@dataclass(frozen=True)
class ResponseStatRecord:
    task_id: int
    response_id: int
    model_config_id: int | None
    model_name: str
    created_at: datetime
    final_score: float | None
    rule_score: float | None
    judge_score: float | None
    status: str = "success"
    score_status: str = "scored"
    excluded_from_stats: bool = False


@dataclass(frozen=True)
class InteractionRecord:
    activity_id: int
    activity_type: Literal["like", "dislike", "comment"]
    user_id: int
    username: str
    task_id: int
    response_id: int
    model_config_id: int | None
    model_name: str
    prompt: str
    content: str | None
    created_at: datetime


@dataclass
class DashboardData:
    summary: FeedbackStatsSummary
    models: list[FeedbackModelStats]
    trend: list[FeedbackTrendPoint]


@dataclass
class _ModelBucket:
    model_config_id: int | None
    model_name: str
    final_scores: list[float] = field(default_factory=list)
    rule_scores: list[float] = field(default_factory=list)
    judge_scores: list[float] = field(default_factory=list)
    call_count: int = 0
    like_count: int = 0
    dislike_count: int = 0
    comment_count: int = 0


@dataclass
class _TrendBucket:
    final_scores: list[float] = field(default_factory=list)
    call_count: int = 0
    like_count: int = 0
    dislike_count: int = 0
    comment_count: int = 0


class FeedbackStatsService:
    def resolve_range(
        self,
        range_name: FeedbackStatsRange,
        now: datetime | None = None,
    ) -> StatsRangeWindow:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_now = current.astimezone(SHANGHAI_TIMEZONE)
        start_at: datetime | None = None
        if range_name != "all":
            days = 7 if range_name == "7d" else 30
            start_date = local_now.date() - timedelta(days=days - 1)
            start_at = datetime.combine(start_date, time.min, tzinfo=SHANGHAI_TIMEZONE)
        return StatsRangeWindow(
            name=range_name,
            start_at=start_at,
            end_at=local_now,
            start_at_utc=self._utc_naive(start_at) if start_at is not None else None,
            end_at_utc=self._utc_naive(local_now),
        )

    async def get_personal_stats(
        self,
        db: AsyncSession,
        user_id: int,
        range_name: FeedbackStatsRange,
    ) -> PersonalFeedbackStatsRead:
        window = self.resolve_range(range_name)
        responses = await self._load_responses(db, window, owner_id=user_id)
        received_feedback = await self._load_feedback(db, window, target_owner_id=user_id)
        received_comments = await self._load_comments(db, window, target_owner_id=user_id)
        own_feedback = await self._load_feedback(db, window, actor_user_id=user_id)
        own_comments = await self._load_comments(db, window, actor_user_id=user_id)
        dashboard = self.build_dashboard(responses, received_feedback, received_comments)
        return PersonalFeedbackStatsRead(
            range=range_name,
            startAt=window.start_at,
            endAt=window.end_at,
            summary=dashboard.summary,
            myInteractions=FeedbackInteractionSummary(
                likeCount=sum(item.activity_type == "like" for item in own_feedback),
                dislikeCount=sum(item.activity_type == "dislike" for item in own_feedback),
                commentCount=len(own_comments),
            ),
            models=dashboard.models,
            trend=dashboard.trend,
        )

    async def get_admin_stats(
        self,
        db: AsyncSession,
        range_name: FeedbackStatsRange,
        activity_type: Literal["all", "like", "dislike", "comment"],
        model_config_id: int | None,
        page: int,
        page_size: int,
        *,
        user_id: int,
    ) -> AdminFeedbackStatsRead:
        window = self.resolve_range(range_name)
        responses = await self._load_responses(db, window)
        # 全局统计仅取无正文记录；互动明细另用 SQL 过滤其他人的私有 RAG。
        feedback = await self._load_feedback(db, window, aggregate_only=True)
        comments = await self._load_comments(db, window, aggregate_only=True)
        dashboard = self.build_dashboard(responses, feedback, comments)
        visible_feedback = await self._load_feedback(db, window, viewer_id=user_id)
        visible_comments = await self._load_comments(db, window, viewer_id=user_id)
        activities = self.filter_activities(
            [*visible_feedback, *visible_comments],
            activity_type=activity_type,
            model_config_id=model_config_id,
            page=page,
            page_size=page_size,
        )
        return AdminFeedbackStatsRead(
            range=range_name,
            startAt=window.start_at,
            endAt=window.end_at,
            summary=dashboard.summary,
            models=dashboard.models,
            trend=dashboard.trend,
            activities=activities,
        )

    def build_dashboard(
        self,
        responses: list[ResponseStatRecord],
        feedback: list[InteractionRecord],
        comments: list[InteractionRecord],
    ) -> DashboardData:
        model_buckets: dict[tuple[int | None, str], _ModelBucket] = {}
        trend_buckets: dict[date, _TrendBucket] = {}

        def model_bucket(model_config_id: int | None, model_name: str) -> _ModelBucket:
            key = (model_config_id, model_name)
            if key not in model_buckets:
                model_buckets[key] = _ModelBucket(model_config_id=model_config_id, model_name=model_name)
            return model_buckets[key]

        for record in responses:
            bucket = model_bucket(record.model_config_id, record.model_name)
            bucket.call_count += 1
            trend = trend_buckets.setdefault(self._shanghai_date(record.created_at), _TrendBucket())
            trend.call_count += 1
            if self._is_scored_record(record):
                bucket.final_scores.append(record.final_score)
                trend.final_scores.append(record.final_score)
            if self._is_scored_record(record) and record.rule_score is not None:
                bucket.rule_scores.append(record.rule_score)
            if self._is_scored_record(record) and record.judge_score is not None:
                bucket.judge_scores.append(record.judge_score)

        for record in [*feedback, *comments]:
            bucket = model_bucket(record.model_config_id, record.model_name)
            trend = trend_buckets.setdefault(self._shanghai_date(record.created_at), _TrendBucket())
            if record.activity_type == "like":
                bucket.like_count += 1
                trend.like_count += 1
            elif record.activity_type == "dislike":
                bucket.dislike_count += 1
                trend.dislike_count += 1
            else:
                bucket.comment_count += 1
                trend.comment_count += 1

        models = [self._serialize_model_bucket(bucket) for bucket in model_buckets.values()]
        models.sort(key=lambda item: (-item.call_count, item.model_name, item.model_config_id or 0))
        final_scores = [
            record.final_score
            for record in responses
            if self._is_scored_record(record)
        ]
        like_count = sum(record.activity_type == "like" for record in feedback)
        dislike_count = sum(record.activity_type == "dislike" for record in feedback)
        summary = FeedbackStatsSummary(
            taskCount=len({record.task_id for record in responses}),
            callCount=len(responses),
            scoredCount=len(final_scores),
            averageFinalScore=self._average(final_scores),
            likeCount=like_count,
            dislikeCount=dislike_count,
            likeRate=self._like_rate(like_count, dislike_count),
            commentCount=len(comments),
        )
        trend = [
            FeedbackTrendPoint(
                date=trend_date,
                callCount=bucket.call_count,
                averageFinalScore=self._average(bucket.final_scores),
                likeCount=bucket.like_count,
                dislikeCount=bucket.dislike_count,
                commentCount=bucket.comment_count,
            )
            for trend_date, bucket in sorted(trend_buckets.items())
        ]
        return DashboardData(summary=summary, models=models, trend=trend)

    def filter_activities(
        self,
        activities: list[InteractionRecord],
        *,
        activity_type: Literal["all", "like", "dislike", "comment"],
        model_config_id: int | None,
        page: int,
        page_size: int,
    ) -> FeedbackActivityListRead:
        filtered = [
            item
            for item in activities
            if (activity_type == "all" or item.activity_type == activity_type)
            and (model_config_id is None or item.model_config_id == model_config_id)
        ]
        filtered.sort(key=lambda item: (item.created_at, item.activity_id), reverse=True)
        start = (page - 1) * page_size
        items = [self._serialize_activity(item) for item in filtered[start : start + page_size]]
        return FeedbackActivityListRead(items=items, total=len(filtered), page=page, pageSize=page_size)

    async def _load_responses(
        self,
        db: AsyncSession,
        window: StatsRangeWindow,
        owner_id: int | None = None,
    ) -> list[ResponseStatRecord]:
        statement = (
            select(
                EvaluationTask.id,
                ModelResponse.id,
                ModelResponse.model_config_id,
                ModelConfig.display_name,
                ModelResponse.config_snapshot,
                ModelResponse.created_at,
                ModelResponse.status,
                EvaluationResult.final_score,
                EvaluationResult.rule_score,
                EvaluationResult.judge_score,
                EvaluationResult.score_status,
                EvaluationResult.excluded_from_stats,
            )
            .select_from(ModelResponse)
            .join(EvaluationTask, EvaluationTask.id == ModelResponse.task_id)
            .outerjoin(EvaluationResult, EvaluationResult.response_id == ModelResponse.id)
            .outerjoin(ModelConfig, ModelConfig.id == ModelResponse.model_config_id)
        )
        statement = self._apply_time_window(statement, ModelResponse.created_at, window)
        if owner_id is not None:
            statement = statement.where(EvaluationTask.user_id == owner_id)
        rows = await db.execute(statement)
        return [
            ResponseStatRecord(
                task_id=int(task_id),
                response_id=int(response_id),
                model_config_id=model_config_id,
                model_name=self._model_name(display_name, snapshot),
                created_at=created_at,
                final_score=self._number(final_score),
                rule_score=self._number(rule_score),
                judge_score=self._number(judge_score),
                status=status,
                score_status=score_status or "scored",
                excluded_from_stats=bool(excluded_from_stats),
            )
            for (
                task_id,
                response_id,
                model_config_id,
                display_name,
                snapshot,
                created_at,
                status,
                final_score,
                rule_score,
                judge_score,
                score_status,
                excluded_from_stats,
            ) in rows.all()
        ]

    async def _load_feedback(
        self,
        db: AsyncSession,
        window: StatsRangeWindow,
        *,
        target_owner_id: int | None = None,
        actor_user_id: int | None = None,
        aggregate_only: bool = False,
        viewer_id: int | None = None,
    ) -> list[InteractionRecord]:
        statement = self._interaction_statement(UserFeedback.id, UserFeedback.feedback_type, UserFeedback.created_at,
            aggregate_only=aggregate_only)
        statement = self._apply_time_window(statement, UserFeedback.created_at, window)
        if target_owner_id is not None:
            statement = statement.where(EvaluationTask.user_id == target_owner_id)
        if actor_user_id is not None:
            statement = statement.where(UserFeedback.user_id == actor_user_id,
                or_(EvaluationTask.task_type == "chat", EvaluationTask.user_id == actor_user_id))
        if viewer_id is not None:
            statement = statement.where(or_(EvaluationTask.task_type == "chat", EvaluationTask.user_id == viewer_id))
        rows = await db.execute(statement)
        return [self._interaction_record(row, content=None) for row in rows.all()]

    async def _load_comments(
        self,
        db: AsyncSession,
        window: StatsRangeWindow,
        *,
        target_owner_id: int | None = None,
        actor_user_id: int | None = None,
        aggregate_only: bool = False,
        viewer_id: int | None = None,
    ) -> list[InteractionRecord]:
        statement = self._interaction_statement(
            UserComment.id,
            None,
            UserComment.created_at,
            content_column=UserComment.content,
            user_id_column=UserComment.user_id,
            response_id_column=UserComment.response_id,
            aggregate_only=aggregate_only,
        )
        statement = self._apply_time_window(statement, UserComment.created_at, window)
        if target_owner_id is not None:
            statement = statement.where(EvaluationTask.user_id == target_owner_id)
        if actor_user_id is not None:
            statement = statement.where(UserComment.user_id == actor_user_id,
                or_(EvaluationTask.task_type == "chat", EvaluationTask.user_id == actor_user_id))
        if viewer_id is not None:
            statement = statement.where(or_(EvaluationTask.task_type == "chat", EvaluationTask.user_id == viewer_id))
        rows = await db.execute(statement)
        return [self._interaction_record(row, content=row[-1], force_type="comment") for row in rows.all()]

    def _interaction_statement(
        self,
        activity_id_column: object,
        activity_type_column: object | None,
        created_at_column: object,
        *,
        content_column: object | None = None,
        user_id_column: object = UserFeedback.user_id,
        response_id_column: object = UserFeedback.response_id,
        aggregate_only: bool = False,
    ) -> object:
        columns = [
            activity_id_column,
            activity_type_column if activity_type_column is not None else UserComment.id,
            user_id_column,
            User.username,
            EvaluationTask.id,
            ModelResponse.id,
            ModelResponse.model_config_id,
            ModelConfig.display_name,
            ModelResponse.config_snapshot,
            literal("") if aggregate_only else EvaluationTask.prompt,
            created_at_column,
        ]
        if content_column is not None:
            columns.append(literal(None) if aggregate_only else content_column)
        return (
            select(*columns)
            .select_from(UserFeedback if activity_type_column is not None else UserComment)
            .join(ModelResponse, ModelResponse.id == response_id_column)
            .join(EvaluationTask, EvaluationTask.id == ModelResponse.task_id)
            .outerjoin(ModelConfig, ModelConfig.id == ModelResponse.model_config_id)
            .outerjoin(User, User.id == user_id_column)
        )

    def _interaction_record(
        self,
        row: object,
        *,
        content: str | None,
        force_type: Literal["comment"] | None = None,
    ) -> InteractionRecord:
        values = tuple(row)
        activity_type = force_type or values[1]
        return InteractionRecord(
            activity_id=int(values[0]),
            activity_type=activity_type,
            user_id=int(values[2]),
            username=values[3] or "anonymous",
            task_id=int(values[4]),
            response_id=int(values[5]),
            model_config_id=values[6],
            model_name=self._model_name(values[7], values[8]),
            prompt=values[9],
            content=content,
            created_at=values[10],
        )

    def _serialize_model_bucket(self, bucket: _ModelBucket) -> FeedbackModelStats:
        return FeedbackModelStats(
            modelConfigId=bucket.model_config_id,
            modelName=bucket.model_name,
            callCount=bucket.call_count,
            scoredCount=len(bucket.final_scores),
            averageFinalScore=self._average(bucket.final_scores),
            averageRuleScore=self._average(bucket.rule_scores),
            averageJudgeScore=self._average(bucket.judge_scores),
            likeCount=bucket.like_count,
            dislikeCount=bucket.dislike_count,
            likeRate=self._like_rate(bucket.like_count, bucket.dislike_count),
            commentCount=bucket.comment_count,
        )

    def _serialize_activity(self, item: InteractionRecord) -> FeedbackActivityRead:
        return FeedbackActivityRead(
            activityId=item.activity_id,
            activityType=item.activity_type,
            userId=item.user_id,
            username=item.username,
            taskId=item.task_id,
            responseId=item.response_id,
            modelConfigId=item.model_config_id,
            modelName=item.model_name,
            prompt=item.prompt,
            content=item.content,
            createdAt=item.created_at,
        )

    def _apply_time_window(self, statement: object, column: object, window: StatsRangeWindow) -> object:
        if window.start_at_utc is not None:
            statement = statement.where(column >= window.start_at_utc)
        return statement.where(column <= window.end_at_utc)

    def _model_name(self, display_name: str | None, snapshot: dict[str, object] | None) -> str:
        values = snapshot or {}
        return display_name or str(values.get("displayName") or values.get("modelName") or "未知模型")

    def _average(self, values: Iterable[float]) -> float | None:
        normalized = list(values)
        if not normalized:
            return None
        return round(sum(normalized) / len(normalized), 2)

    def _is_scored_record(self, record: ResponseStatRecord) -> bool:
        return (
            record.status == "success"
            and record.score_status == "scored"
            and not record.excluded_from_stats
            and record.final_score is not None
        )

    def _like_rate(self, like_count: int, dislike_count: int) -> float | None:
        total = like_count + dislike_count
        return round(like_count / total, 4) if total else None

    def _number(self, value: Decimal | int | float | None) -> float | None:
        return float(value) if value is not None else None

    def _shanghai_date(self, value: datetime) -> date:
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(SHANGHAI_TIMEZONE).date()

    def _utc_naive(self, value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(tzinfo=None)


feedback_stats_service = FeedbackStatsService()
