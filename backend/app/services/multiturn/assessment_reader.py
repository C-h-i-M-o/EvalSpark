"""按会话当前权限提供只读评分投影，不触发供应商调用。"""

from sqlalchemy import func, select
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationAssessment, ConversationJudgeRun
from app.schemas.conversation import (
    AssessmentDetailRead, AssessmentListRead, AssessmentRead, AssessmentRunRead,
)
from app.services.multiturn.store import ConversationError, ConversationStore


def project_assessment(job: ConversationAssessment) -> AssessmentRead:
    """显式选择允许公开的字段，避免泄漏内部评分输入。"""
    return AssessmentRead(id=job.id, responseId=job.response_id,
        modelConfigId=job.model_config_id, throughTurn=job.through_turn,
        scoreVersion=job.score_version, status=job.status, formal=job.formal,
        result=job.result_json, createdAt=job.created_at, completedAt=job.completed_at)


class AssessmentReader:
    """列表和详情都在查询评分前检查会话读取权限。"""

    async def list(self, db: AsyncSession, conversation_id: int, user_id: int, *,
                   page: int = 1, page_size: int = 20,
                   response_id: int | None = None,
                   scope: Literal["dialogue", "session"] | None = None) -> AssessmentListRead:
        """按回答可选筛选并稳定倒序分页，避免无界加载历史。"""
        await ConversationStore().get(db, conversation_id, user_id)
        if page < 1 or not 1 <= page_size <= 100:
            raise ConversationError("assessment_invalid_page", "评分分页参数无效", 422)
        conditions = [ConversationAssessment.conversation_id == conversation_id]
        if response_id is not None:
            conditions.append(ConversationAssessment.response_id == response_id)
        if scope is not None:
            conditions.append(ConversationAssessment.input_json["packet"]["scope"].as_string() == scope)
        total = await db.scalar(select(func.count()).select_from(ConversationAssessment).where(*conditions))
        jobs = (await db.scalars(select(ConversationAssessment).where(*conditions)
            .order_by(ConversationAssessment.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        return AssessmentListRead(items=[project_assessment(job) for job in jobs],
                                  total=total or 0, page=page, pageSize=page_size)

    async def get(self, db: AsyncSession, conversation_id: int, assessment_id: int,
                  user_id: int) -> AssessmentDetailRead:
        """同时限定会话和作业 ID，禁止通过跨会话 ID 读取评分。"""
        await ConversationStore().get(db, conversation_id, user_id)
        job = await db.scalar(select(ConversationAssessment).where(
            ConversationAssessment.id == assessment_id,
            ConversationAssessment.conversation_id == conversation_id))
        if job is None:
            raise ConversationError("assessment_not_found", "评分作业不存在或无权访问", 404)
        runs = (await db.scalars(select(ConversationJudgeRun).where(
            ConversationJudgeRun.assessment_id == job.id).order_by(ConversationJudgeRun.run_index))).all()
        return AssessmentDetailRead(**project_assessment(job).model_dump(), runs=[
            AssessmentRunRead(runIndex=run.run_index, status=run.status,
                              result=run.result_json, errorCode=run.error_code) for run in runs])


assessment_reader = AssessmentReader()
