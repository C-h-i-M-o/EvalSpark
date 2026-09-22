"""普通与 RAG 共用会话元数据接口。"""

from typing import Literal
import json
from collections.abc import AsyncIterator

import anyio

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.conversation import ConversationTurn

from app.api.dependencies import get_current_user
from app.db.session import get_db, AsyncSessionLocal
from app.models.user import User
from app.schemas.conversation import ConversationCreate, ConversationListRead, ConversationRead, TurnListRead, TurnCreate
from app.schemas.evaluation import EvaluationTaskVisibilityUpdate
from app.services.multiturn.catalog import conversation_catalog
from app.services.multiturn.generation import ConversationGenerator
from app.services.multiturn.rag_generation import RagConversationGenerator
from app.services.multiturn.store import ConversationStore
from app.services.multiturn.assessment_reader import assessment_reader
from app.schemas.conversation import AssessmentDetailRead, AssessmentListRead
from app.schemas.conversation import AssessmentCreate, AssessmentRead
from app.services.multiturn.reassessment import submit_reassessment
from app.services.multiturn.reports import submit_report
from app.schemas.conversation import ReportCreate, BranchActionCreate
from app.services.multiturn.store import ConversationError
from app.services.multiturn.branch_actions import reserve_branch_action
from app.services.model_config_service import ModelConfigServiceError
from app.services.token_quota_service import TokenQuotaExceededError

router = APIRouter()
conversation_generator = ConversationGenerator(AsyncSessionLocal)
rag_conversation_generator = RagConversationGenerator(AsyncSessionLocal)
conversation_store = ConversationStore()


@router.post("/conversations/{conversation_id}/reports", response_model=AssessmentRead, status_code=202)
async def create_conversation_report(conversation_id: int, payload: ReportCreate,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> AssessmentRead:
    """作者提交固定分支和截止轮次报告，创建后异步执行三次完整评审。"""
    return await submit_report(db, conversation_id, current_user.id, payload)


@router.get("/conversations/{conversation_id}/reports", response_model=AssessmentListRead)
async def list_conversation_reports(conversation_id: int, page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100, alias="pageSize"),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> AssessmentListRead:
    """只读当前可见会话的报告列表，不混入单轮评分或触发模型调用。"""
    return await assessment_reader.list(db, conversation_id, current_user.id,
        page=page, page_size=page_size, scope="session")


@router.post("/conversations/{conversation_id}/assessments", response_model=AssessmentRead, status_code=202)
async def create_conversation_assessment(conversation_id: int, payload: AssessmentCreate,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> AssessmentRead:
    """提交既有暂定评分的正式复评，重复请求返回同一个作业。"""
    return await submit_reassessment(db, conversation_id, current_user.id, payload.source_assessment_id)


@router.get("/conversations/{conversation_id}/assessments", response_model=AssessmentListRead)
async def list_conversation_assessments(conversation_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    response_id: int | None = Query(default=None, gt=0, alias="responseId"),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> AssessmentListRead:
    """分页读取当前可见会话的评分，读取不触发收费评审。"""
    return await assessment_reader.list(db, conversation_id, current_user.id,
                                        page=page, page_size=page_size, response_id=response_id)


@router.get("/conversations/{conversation_id}/assessments/{assessment_id}", response_model=AssessmentDetailRead)
async def get_conversation_assessment(conversation_id: int, assessment_id: int,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> AssessmentDetailRead:
    """公开详情仅返回评分投影和逐次结果，不返回评审输入快照。"""
    return await assessment_reader.get(db, conversation_id, assessment_id, current_user.id)


@router.post("/conversations/{conversation_id}/turns/stream")
async def stream_conversation_turn(conversation_id: int, payload: TurnCreate,
                                    db: AsyncSession = Depends(get_db),
                                    current_user: User = Depends(get_current_user)) -> StreamingResponse:
    """权限与额度预检在响应头之前完成，连接结束时关闭内部生成器。"""
    owner = current_user.id
    conversation = await conversation_store.get(db, conversation_id, owner, owner_only=True)
    generator = rag_conversation_generator if conversation.mode == "rag" else conversation_generator
    # 生成器使用独立短事务，流式连接不能保留分发查询的读事务。
    await db.rollback()
    events = generator.stream(conversation_id, owner, payload)
    return await render_conversation_stream(events)


@router.post("/conversations/{conversation_id}/branches/stream")
async def stream_conversation_branch(conversation_id: int, payload: BranchActionCreate,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> StreamingResponse:
    """作者只提交分支操作，问题与历史从服务端原轮读取，不接受客户端改写。"""
    owner = current_user.id
    conversation = await conversation_store.get(db, conversation_id, owner, owner_only=True)
    turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == payload.turn_id,
        ConversationTurn.conversation_id == conversation_id))
    if turn is None:
        raise ConversationError("conversation_turn_not_found", "会话轮次不存在", 404)
    question = TurnCreate(prompt=turn.prompt, expectedTurn=turn.turn_index - 1, requestKey=payload.request_key)
    generator = rag_conversation_generator if conversation.mode == "rag" else conversation_generator
    await db.rollback()
    if payload.action == "skip":
        return await render_conversation_stream(skip_conversation_branch(conversation_id, owner, payload))
    return await render_conversation_stream(generator.stream(conversation_id, owner, question, branch_action=payload))


async def skip_conversation_branch(conversation_id: int, owner: int,
                                    payload: BranchActionCreate) -> AsyncIterator[dict[str, object]]:
    """跳过不解析供应商凭据或检索知识库，禁用模型也能明确结束失败分支。"""
    async with AsyncSessionLocal() as db:
        turn, response, _, created = await reserve_branch_action(db, conversation_id, owner, payload, None)
        turn_id, task_id, index, response_id = turn.id, turn.task_id, turn.turn_index, response.id
    yield {"type": "turn_started", "turnId": turn_id, "taskId": task_id, "turn": index, "replayed": not created}
    if created:
        yield {"type": "answer_completed", "modelConfigId": payload.model_config_id,
            "responseId": response_id, "status": "failed", "errorCode": "branch_skipped"}
        yield {"type": "turn_completed", "turnId": turn_id, "taskId": task_id}


async def render_conversation_stream(events: AsyncIterator[dict[str, object]]) -> StreamingResponse:
    """普通续聊与失败恢复共用预检、错误事件和断连关闭协议。"""
    try:
        first = await anext(events)
    except TokenQuotaExceededError as error:
        await events.aclose()
        raise HTTPException(status_code=429, detail="今日 Token 额度已用完") from error
    except ModelConfigServiceError as error:
        await events.aclose()
        raise HTTPException(status_code=422, detail="会话模型配置不存在或不可用") from error
    except BaseException:
        await events.aclose()
        raise

    async def render() -> AsyncIterator[str]:
        """流已开始后的异常使用稳定错误码，清理不受断连取消范围打断。"""
        try:
            yield json.dumps(first, ensure_ascii=False) + "\n"
            async for event in events:
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception:
            yield json.dumps({"type": "stream_error", "code": "generation_interrupted",
                              "message": "生成中断，请从历史记录确认各模型状态"}, ensure_ascii=False) + "\n"
        finally:
            with anyio.CancelScope(shield=True):
                await events.aclose()

    return StreamingResponse(render(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/conversations", response_model=ConversationRead, status_code=201)
async def create_conversation(payload: ConversationCreate, db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_user)) -> ConversationRead:
    """创建会话仅固定配置，不调用供应商或自动生成首轮。"""
    return await conversation_catalog.create(db, current_user.id, payload)


@router.get("/conversations", response_model=ConversationListRead)
async def list_conversations(task_type: Literal["chat", "rag"] = Query(default="chat", alias="taskType"),
                             page: int = Query(default=1, ge=1),
                             page_size: int = Query(default=10, ge=1, le=100, alias="pageSize"),
                             db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> ConversationListRead:
    """按普通/RAG 工作台分别列出当前用户可读的会话。"""
    return await conversation_catalog.list(db, current_user.id, mode=task_type, page=page, page_size=page_size)


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db),
                           current_user: User = Depends(get_current_user)) -> ConversationRead:
    """读取会话状态和非秘密配置，公开读者的 canContinue 始终为假。"""
    return await conversation_catalog.get(db, conversation_id, current_user.id)


@router.get("/conversations/{conversation_id}/turns", response_model=TurnListRead)
async def get_conversation_turns(conversation_id: int, page: int = Query(default=1, ge=1),
                                 page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
                                 db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> TurnListRead:
    """分页返回轮次摘要，通过 taskId 获取已授权的回答详情。"""
    return await conversation_catalog.turns(db, conversation_id, current_user.id, page=page, page_size=page_size)


@router.patch("/conversations/{conversation_id}/visibility", response_model=ConversationRead)
async def update_conversation_visibility(conversation_id: int, payload: EvaluationTaskVisibilityUpdate,
                                         db: AsyncSession = Depends(get_db),
                                         current_user: User = Depends(get_current_user)) -> ConversationRead:
    """作者统一修改会话和全部轮次任务的可见性。"""
    return await conversation_catalog.set_visibility(db, conversation_id, current_user.id, payload.visibility)
