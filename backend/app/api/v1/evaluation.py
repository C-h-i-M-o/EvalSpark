import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi import Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.evaluation import (
    CommentCreate,
    CommentListRead,
    CommentRead,
    EvaluationTaskCreate,
    EvaluationTaskListRead,
    EvaluationTaskRead,
    EvaluationTaskVisibilityUpdate,
    FeedbackCreate,
    FeedbackToggleRead,
)
from app.services.evaluation_service import (
    EvaluationCommentNotFoundError,
    EvaluationResponseNotFoundError,
    EvaluationTaskNotFoundError,
    EvaluationTaskValidationError,
    evaluation_service,
)
from app.services.token_quota_service import TokenQuotaExceededError, token_quota_service
from app.services.rag.service import rag_evaluation_service

router = APIRouter()


@router.post("/tasks", response_model=EvaluationTaskRead)
async def create_evaluation_task(
    payload: EvaluationTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvaluationTaskRead:
    try:
        await token_quota_service.ensure_can_start(db, current_user)
        return await evaluation_service.create_task(payload, db, current_user.id, current_user.username)
    except TokenQuotaExceededError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except EvaluationTaskValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/tasks/stream")
async def stream_evaluation_task(
    payload: EvaluationTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    rag_run = None
    user_id, username = current_user.id, current_user.username
    try:
        await token_quota_service.ensure_can_start(db, current_user)
        await evaluation_service.validate_task_models(payload, db)
        if payload.task_type == "rag":
            rag_run = await rag_evaluation_service.start(payload, db, user_id)
    except TokenQuotaExceededError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except EvaluationTaskValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    async def render_events() -> AsyncIterator[str]:
        events = evaluation_service.stream_task_events(
            payload,
            db,
            user_id,
            username,
            **({"rag_run": rag_run} if rag_run is not None else {}),
        )
        async with aclosing(events):
            async for event in events:
                yield json.dumps(jsonable_encoder(event), ensure_ascii=False) + "\n"

    return StreamingResponse(render_events(), media_type="application/x-ndjson")


@router.get("/tasks", response_model=EvaluationTaskListRead)
async def list_evaluation_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100, alias="pageSize"),
    task_type: Literal["chat", "rag"] | None = Query(default=None, alias="taskType"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvaluationTaskListRead:
    if task_type is not None:
        return await evaluation_service.list_tasks(db, page, page_size, current_user.id, task_type=task_type)
    return await evaluation_service.list_tasks(db, page, page_size, current_user.id)


@router.get("/tasks/{task_id}", response_model=EvaluationTaskRead)
async def get_evaluation_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvaluationTaskRead:
    try:
        return await evaluation_service.get_task(task_id, db, current_user.id)
    except EvaluationTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/tasks/{task_id}/visibility", response_model=EvaluationTaskRead)
async def update_evaluation_visibility(
    task_id: int,
    payload: EvaluationTaskVisibilityUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvaluationTaskRead:
    try:
        return await evaluation_service.update_task_visibility(task_id, payload.visibility, db, current_user.id)
    except EvaluationTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/responses/{response_id}/feedback", response_model=FeedbackToggleRead)
async def create_feedback(
    response_id: int,
    payload: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FeedbackToggleRead:
    try:
        return await evaluation_service.toggle_response_feedback(
            response_id,
            payload,
            db,
            current_user.id,
        )
    except EvaluationResponseNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/responses/{response_id}/comments", response_model=CommentListRead)
async def list_response_comments(
    response_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentListRead:
    try:
        return await evaluation_service.list_response_comments(
            response_id,
            db,
            page,
            page_size,
            current_user.id,
        )
    except EvaluationResponseNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/responses/{response_id}/comments", response_model=CommentRead, status_code=201)
async def create_response_comment(
    response_id: int,
    payload: CommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentRead:
    try:
        return await evaluation_service.create_response_comment(
            response_id,
            payload,
            db,
            current_user.id,
            current_user.username,
        )
    except EvaluationResponseNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_response_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        await evaluation_service.delete_response_comment(comment_id, db, current_user.id)
    except EvaluationCommentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)
