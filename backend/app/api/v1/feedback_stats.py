from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.feedback_stats import AdminFeedbackStatsRead, PersonalFeedbackStatsRead
from app.services.feedback_stats_service import feedback_stats_service

router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/me", response_model=PersonalFeedbackStatsRead)
async def get_personal_feedback_stats(
    range_name: Literal["7d", "30d", "all"] = Query(default="30d", alias="range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PersonalFeedbackStatsRead:
    return await feedback_stats_service.get_personal_stats(db, current_user.id, range_name)


@admin_router.get("", response_model=AdminFeedbackStatsRead)
async def get_admin_feedback_stats(
    range_name: Literal["7d", "30d", "all"] = Query(default="30d", alias="range"),
    activity_type: Literal["all", "like", "dislike", "comment"] = Query(
        default="all",
        alias="activityType",
    ),
    model_config_id: int | None = Query(default=None, ge=1, alias="modelConfigId"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> AdminFeedbackStatsRead:
    return await feedback_stats_service.get_admin_stats(
        db,
        range_name,
        activity_type,
        model_config_id,
        page,
        page_size,
        user_id=current_user.id,
    )
