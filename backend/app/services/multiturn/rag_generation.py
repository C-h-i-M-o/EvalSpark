"""RAG 多轮生成协调器，供共用会话 HTTP 入口调用。"""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Literal

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ModelRequest
from app.adapters.openai_compatible import OpenAICompatibleClient
from app.models.user import User
from app.models.conversation import ConversationContext
from app.schemas.conversation import TurnCreate, BranchActionCreate
from app.schemas.rag import (
    PreparedRagResponse, RagAnswerCompletedEvent, RagAnswerReadyEvent,
    RagDeltaEvent, RagStreamEvent, RagTaskContext, RagEvidence,
)
from app.services.model_config_service import RuntimeModelConfig
from app.services.multiturn.catalog import knowledge_snapshot
from app.services.multiturn.context_status import project_context_status
from app.services.multiturn.dispatch import resolve_judge
from app.services.multiturn.heartbeat import start_generation_heartbeat
from app.services.multiturn.rag_context import RagContextBuilder
from app.services.multiturn.rag_reference_store import load_historical_references
from app.services.multiturn.rag_references import merge_reference_evidence
from app.services.multiturn.runtime import resolve_frozen_model
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.multiturn.turn_scoring import prepare_turn_requirements, submit_turn_assessments, load_turn_memory
from app.services.multiturn.branch_actions import reserve_branch_action, settle_branch_action
from app.services.rag.evaluation import RagEvaluationRunner, create_client
from app.services.rag.evaluation_store import RagEvaluationStore
from app.services.token_quota_service import token_quota_service


class RagConversationGenerator:
    """复用 RAG 检索、阶段流水与多轮存储，避免重复任务和重复计费。"""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *,
                 client_factory: Callable[[RuntimeModelConfig], OpenAICompatibleClient] = create_client,
                 runner_factory: Callable[[RagEvaluationStore], RagEvaluationRunner] | None = None) -> None:
        """允许隔离测试替换外部检索客户端，数据库仍执行真实持久化。"""
        self.sessions, self.client_factory = sessions, client_factory
        self.store = ConversationStore()
        self.rag_store = RagEvaluationStore(sessions)
        self.runner = runner_factory(self.rag_store) if runner_factory else RagEvaluationRunner(
            self.rag_store, client_factory=client_factory)

    async def _ensure_quota(self, owner: int) -> None:
        """每个收费阶段重新确认账号与日额度，避免沿用会话开始时的状态。"""
        async with self.sessions() as db:
            user = await db.get(User, owner, populate_existing=True)
            if user is None or user.status != "active":
                raise ConversationError("conversation_user_disabled", "用户不存在或已停用", 403)
            await token_quota_service.ensure_can_start(db, user)

    async def stream(self, conversation_id: int, owner: int, payload: TurnCreate, *,
                     branch_action: BranchActionCreate | None = None) -> AsyncIterator[dict[str, object]]:
        """串行预留轮次，分支并发检索生成，断连时停止请求并释放会话锁。"""
        async with self.sessions() as db:
            conversation = await self.store.get(db, conversation_id, owner, owner_only=True)
            config = conversation.config_json or {}
            knowledge_id = config.get("knowledgeBaseId")
            ids = config.get("modelIds")
            if conversation.mode != "rag" or type(knowledge_id) is not int or not isinstance(ids, list) or not ids:
                raise ConversationError("conversation_config_invalid", "RAG 会话配置不完整", 409)
            if await knowledge_snapshot(db, knowledge_id, owner) != conversation.knowledge_snapshot_json:
                raise ConversationError("knowledge_base_changed", "知识版本已变化，请创建新会话", 409)
            if branch_action is not None:
                if branch_action.model_config_id not in ids:
                    raise ConversationError("conversation_invalid_branch", "模型分支不属于当前会话", 422)
                ids = [branch_action.model_config_id]
            models = [await resolve_frozen_model(db, conversation, identity) for identity in ids]
            summary = await resolve_frozen_model(db, conversation, config.get("summaryModelId"))
            judge = await resolve_judge(db, conversation)
            references = {model.id: await load_historical_references(db, conversation_id, owner, model.id,
                before_turn=payload.expected_turn + 1, prompt=payload.prompt) for model in models}
            await db.rollback()
        if branch_action is None or branch_action.action == "retry":
            await self._ensure_quota(owner)
        async with self.sessions() as db:
            response_id, attempt = None, 1
            if branch_action is None:
                turn, created = await self.store.reserve_turn(db, conversation_id, owner, prompt=payload.prompt,
                    expected_turn=payload.expected_turn, request_key=payload.request_key)
            else:
                turn, response, attempt, created = await reserve_branch_action(db, conversation_id, owner, branch_action, models[0])
                response_id = response.id
            turn_id, task_id, turn_index = turn.id, turn.task_id, turn.turn_index
            generation_epoch = turn.generation_epoch
        context: RagTaskContext | None = None
        producer: asyncio.Task[None] | None = None
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue(maxsize=128)
        finished = False
        consumer_closed = False
        heartbeat = start_generation_heartbeat(self.sessions, conversation_id, turn_id, owner, generation_epoch=generation_epoch) if created and (
            branch_action is None or branch_action.action == "retry") else None
        try:
            yield {"type": "turn_started", "turnId": turn_id, "taskId": task_id,
                   "turn": turn_index, "replayed": not created}
            if not created:
                return
            if branch_action is not None and branch_action.action == "skip":
                finished = True
                yield {"type": "answer_completed", "modelConfigId": models[0].id,
                    "responseId": response_id, "status": "failed", "errorCode": "branch_skipped"}
                yield {"type": "turn_completed", "turnId": turn_id, "taskId": task_id}
                return
            context, prepared = await self.rag_store.create(owner, knowledge_id, payload.prompt, models,
                enable_thinking=bool(config.get("enableThinking")), conversation_id=conversation_id,
                reserved_turn_id=turn_id, reserved_response_id=response_id, generation_epoch=generation_epoch)
            memory = await load_turn_memory(self.sessions, conversation_id, owner, turn_index) if branch_action else (
                await prepare_turn_requirements(self.sessions, conversation_id=conversation_id, owner=owner,
                    turn_id=turn_id, branch_id=models[0].id, model=summary, client=self.client_factory(summary),
                    budget=int(config.get("inputBudget", 8192))))
            builder = RagContextBuilder(self.sessions, conversation_id=conversation_id, owner=owner,
                turn_id=turn_id, summary_model=summary, summary_client=self.client_factory(summary), memory=memory,
                references=references, attempt=attempt, generation_epoch=generation_epoch)

            async def merge_evidence(scope: RagTaskContext, item: PreparedRagResponse) -> list[RagEvidence]:
                """固定本轮快照前合并已验证历史原文，仍保留新检索与版本屏障。"""
                if scope.task_id != context.task_id:
                    raise ConversationError("rag_reference_invalid", "历史引用不属于当前任务", 409)
                return merge_reference_evidence(references.get(item.model_config_id, ()), item.evidence)

            async def quota() -> None:
                """供 Embedding 阶段复用当前用户额度校验。"""
                await self._ensure_quota(owner)

            async def emit_context(model_id: int, phase: Literal["rewrite", "answer"]) -> None:
                """阶段请求已持久化后投影公开状态，关闭事务再等待队列。"""
                async with self.sessions() as db:
                    saved = await db.scalar(select(ConversationContext).where(
                        ConversationContext.conversation_id == conversation_id,
                        ConversationContext.turn_id == turn_id, ConversationContext.model_config_id == model_id,
                        ConversationContext.attempt == builder.attempt))
                    statuses = project_context_status(saved) if saved is not None else []
                for status in statuses:
                    if status.phase == phase:
                        await queue.put({"type": "context_ready", **status.model_dump(mode="json", by_alias=True)})

            async def rewrite(model: RuntimeModelConfig, scope: RagTaskContext, item: PreparedRagResponse) -> ModelRequest:
                """构建改写上下文后再次检查额度，摘要费用可能改变剩余额度。"""
                request = await builder.rewrite(model, scope, item)
                await emit_context(model.id, "rewrite")
                await quota()
                return request

            async def answer(model: RuntimeModelConfig, scope: RagTaskContext, item: PreparedRagResponse) -> ModelRequest:
                """构建带证据的回答上下文后重新检查实际调用资格。"""
                request = await builder.answer(model, scope, item)
                await emit_context(model.id, "answer")
                await quota()
                return request

            async def emit(event: RagStreamEvent) -> None:
                """将检索阶段事件转换为 NDJSON 使用的公开协议。"""
                await queue.put(event.model_dump(mode="json", by_alias=True))

            async def produce() -> None:
                """驱动检索与回答，结算完成后才发布回答终态。"""
                try:
                    await self.runner.prepare_rag_snapshots(context, prepared, models, emit=emit,
                        rewrite_request_builder=rewrite, before_embedding=quota, evidence_transform=merge_evidence)
                    by_response = {item.response_id: item for item in prepared}
                    async for event in self.runner.stream_rag_answers(context, prepared, models, answer_request_builder=answer):
                        if isinstance(event, RagAnswerReadyEvent):
                            await self.rag_store.finalize_multiturn_response(context, event.response_id)
                            item = by_response[event.response_id]
                            await queue.put({"type": "answer_completed", "modelConfigId": event.model_config_id,
                                "responseId": event.response_id, "status": "failed" if item.failure_stage else "success",
                                "errorCode": item.error_code})
                        elif isinstance(event, RagDeltaEvent):
                            await queue.put({"type": "delta", "modelConfigId": event.model_config_id, "delta": event.delta})
                        elif not isinstance(event, RagAnswerCompletedEvent):
                            await emit(event)
                finally:
                    if not consumer_closed:
                        await queue.put(None)

            producer = asyncio.create_task(produce())
            while (event := await queue.get()) is not None:
                yield event
            await producer
            async with self.sessions() as db:
                await self.store.finish_generation(db, conversation_id, turn_id, owner, status="completed", generation_epoch=generation_epoch)
            finished = True
            if judge is not None:
                try:
                    jobs = await submit_turn_assessments(self.sessions, conversation_id=conversation_id, owner=owner,
                        turn_id=turn_id, judge=judge, budget=int(config.get("inputBudget", 8192)),
                        only_response_id=response_id if branch_action else None)
                    yield {"type": "assessments_queued", "turnId": turn_id, "assessmentIds": jobs}
                except Exception:
                    yield {"type": "assessment_submission_failed", "turnId": turn_id,
                           "message": "回答已保存，评分提交未完成"}
            yield {"type": "turn_completed", "turnId": turn_id, "taskId": task_id}
        finally:
            if heartbeat is not None:
                with anyio.CancelScope(shield=True):
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            if created and not finished:
                with anyio.CancelScope(shield=True):
                    consumer_closed = True
                    if producer is not None:
                        producer.cancel()
                        await asyncio.gather(producer, return_exceptions=True)
                    try:
                        if context is not None:
                            await self.rag_store.interrupt(context)
                    finally:
                        if response_id is not None:
                            async with self.sessions() as db:
                                await settle_branch_action(db, conversation_id, owner, turn_id, response_id, generation_epoch)
                        async with self.sessions() as db:
                            await self.store.finish_generation(db, conversation_id, turn_id, owner,
                                status="interrupted", error_code="generation_interrupted", generation_epoch=generation_epoch)
