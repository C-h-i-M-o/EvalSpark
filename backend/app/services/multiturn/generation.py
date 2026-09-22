"""普通多轮并发生成协调；评分独立于生成完成状态。"""
import asyncio
import anyio
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ChatMessage, ModelReply, ModelRequest
from app.adapters.openai_compatible import OpenAICompatibleClient
from app.models.conversation import ConversationTurn, ConversationUsage
from app.models.evaluation import EvaluationResult
from app.models.response import ModelResponse
from app.models.user import User
from app.schemas.conversation import TurnCreate, BranchActionCreate
from app.services.evaluation_service import BUILTIN_SYSTEM_PROMPT
from app.services.model_config_service import RuntimeModelConfig
from app.services.multiturn.history import prepare_context_with_summary
from app.services.multiturn.heartbeat import start_generation_heartbeat
from app.services.multiturn.runtime import resolve_frozen_model
from app.services.multiturn.dispatch import resolve_judge
from app.services.multiturn.turn_scoring import prepare_turn_requirements, submit_turn_assessments, load_turn_memory
from app.services.multiturn.branch_actions import reserve_branch_action, settle_branch_action
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.rag.evaluation import create_client
from app.services.rag.usage import estimate_stage_cost, model_snapshot
from app.services.token_quota_service import token_quota_service


class ConversationGenerator:
    """每个新请求独立协调分支，数据库轮次锁负责阻止重复生成。"""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *,
                 client_factory: Callable[[RuntimeModelConfig], OpenAICompatibleClient] = create_client) -> None:
        """复用供应商适配器并允许隔离测试注入确定性流式模型。"""
        self.sessions, self.client_factory = sessions, client_factory
        self.store = ConversationStore()

    async def stream(self, conversation_id: int, owner: int, payload: TurnCreate, *,
                     branch_action: BranchActionCreate | None = None) -> AsyncIterator[dict[str, object]]:
        """校验配置后预留轮次；重复请求返回原标识，不重跑供应商。"""
        async with self.sessions() as db:
            conversation = await self.store.get(db, conversation_id, owner, owner_only=True)
            if conversation.mode != "chat":
                raise ConversationError("conversation_mode_mismatch", "当前会话应使用 RAG 多轮生成入口", 409)
            config = conversation.config_json or {}
            candidate_ids = config.get("modelIds", [])
            if not isinstance(candidate_ids, list) or not candidate_ids:
                raise ConversationError("conversation_config_invalid", "会话缺少候选模型", 409)
            if branch_action is not None:
                if branch_action.model_config_id not in candidate_ids:
                    raise ConversationError("conversation_invalid_branch", "模型分支不属于当前会话", 422)
                candidate_ids = [branch_action.model_config_id]
            models = [await resolve_frozen_model(db, conversation, value) for value in candidate_ids]
            summary = await resolve_frozen_model(db, conversation, config.get("summaryModelId"))
            judge = await resolve_judge(db, conversation) if config.get("judgeModelId") is not None else None
            user = await db.get(User, owner, populate_existing=True)
            if user is None or user.status != "active":
                raise ConversationError("conversation_user_disabled", "用户不存在或已停用", 403)
            if branch_action is None or branch_action.action == "retry":
                await token_quota_service.ensure_can_start(db, user)
            await db.rollback()
            response_id, attempt = None, 1
            if branch_action is None:
                turn, created = await self.store.reserve_turn(db, conversation_id, owner, prompt=payload.prompt,
                    expected_turn=payload.expected_turn, request_key=payload.request_key)
            else:
                turn, response, attempt, created = await reserve_branch_action(db, conversation_id, owner, branch_action, models[0])
                response_id = response.id
            turn_id, task_id, turn_index = turn.id, turn.task_id, turn.turn_index
            generation_epoch = turn.generation_epoch
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=128)
        tasks: list[asyncio.Task[None]] = []
        finished = False
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
            memory = await load_turn_memory(self.sessions, conversation_id, owner, turn_index) if branch_action else (
                await prepare_turn_requirements(self.sessions, conversation_id=conversation_id, owner=owner,
                    turn_id=turn_id, branch_id=models[0].id, model=summary, client=self.client_factory(summary),
                    budget=int(config.get("inputBudget", 8192))))
            for model in models:
                tasks.append(asyncio.create_task(self._branch(queue, conversation_id, owner, turn_id, task_id,
                    model, summary, int(config.get("inputBudget", 8192)), bool(config.get("enableThinking", False)), memory,
                    response_id=response_id, attempt=attempt, generation_epoch=generation_epoch)))
            remaining = len(tasks)
            while remaining:
                event = await queue.get()
                if event["type"] == "branch_done":
                    remaining -= 1
                else:
                    yield event
            await asyncio.gather(*tasks)
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
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if response_id is not None:
                        async with self.sessions() as db:
                            await settle_branch_action(db, conversation_id, owner, turn_id, response_id, generation_epoch)
                    async with self.sessions() as db:
                        await self.store.finish_generation(db, conversation_id, turn_id, owner,
                                                           status="interrupted", error_code="generation_interrupted", generation_epoch=generation_epoch)

    async def _branch(self, queue: asyncio.Queue[dict[str, object]], conversation_id: int, owner: int,
                      turn_id: int, task_id: int, model: RuntimeModelConfig, summary: RuntimeModelConfig,
                      budget: int, thinking: bool, memory: tuple[ChatMessage, ...] = (), *,
                      response_id: int | None = None, attempt: int = 1, generation_epoch: int = 1) -> None:
        """独立分支先准备上下文，完整回答持久化后才允许后续轮次引用。"""
        reply: ModelReply | None = None
        call_started = False
        error_code: str | None = None
        cancelled = False
        try:
            async with self.sessions() as db:
                await self.store.require_generating(db, conversation_id, turn_id, owner, generation_epoch=generation_epoch)
                if response_id is None:
                    response = ModelResponse(task_id=task_id, model_config_id=model.id, status="pending",
                        config_snapshot=model_snapshot(model).model_dump(mode="json", by_alias=True), currency=model.currency)
                    db.add(response)
                    await db.flush()
                    response_id = response.id
                else:
                    response = await db.get(ModelResponse, response_id)
                    if response is None or response.task_id != task_id or response.model_config_id != model.id or response.status != "pending":
                        raise ConversationError("branch_attempt_conflict", "重试回答已结束或不属于当前分支", 409)
                await db.commit()
            context = await prepare_context_with_summary(self.sessions, conversation_id, owner, turn_id, model.id,
                system_prompt=BUILTIN_SYSTEM_PROMPT, summary_model=summary, summary_client=self.client_factory(summary),
                summary_input_budget=budget, memory_budget=max(256, budget // 4), memory=memory, attempt=attempt,
                generation_epoch=generation_epoch)
            await queue.put({"type": "context_ready", "modelConfigId": model.id, "compressed": context.compressed})
            async with self.sessions() as db:
                await self.store.require_generating(db, conversation_id, turn_id, owner, generation_epoch=generation_epoch)
                user = await db.get(User, owner, populate_existing=True)
                if user is None or user.status != "active":
                    raise ValueError("用户已停用")
                await token_quota_service.ensure_can_start(db, user)
                db.add(ConversationUsage(conversation_id=conversation_id, user_id=owner,
                    operation_key=f"generate:{response_id}", stage="generate", status="pending", currency=model.currency,
                    detail_json={"turnId": turn_id, "responseId": response_id, "model": model_snapshot(model).model_dump(mode="json", by_alias=True)},
                    accounted=False))
                await db.commit()
            call_started = True
            client = self.client_factory(model)
            async with asyncio.timeout(model.timeout_seconds + 5):
                async for event in client.stream_chat(ModelRequest(prompt="", model_name=model.model_name,
                    messages=context.messages, max_tokens=model.max_tokens, temperature=model.temperature,
                    extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}})):
                    if event.reply is not None:
                        reply = event.reply
                    if event.delta:
                        await queue.put({"type": "delta", "modelConfigId": model.id, "delta": event.delta})
            if reply is None or not reply.answer.strip():
                raise ValueError("模型未返回完整回答")
            if len(reply.answer.encode("utf-8")) > 65535:
                raise ValueError("回答超过当前持久化长度")
        except asyncio.CancelledError:
            cancelled, error_code = True, "generation_interrupted"
        except Exception:
            error_code = "generation_failed"
        finally:
            try:
                if response_id is not None:
                    await self._save(conversation_id, owner, response_id, model, reply, call_started, error_code)
                if not cancelled:
                    await queue.put({"type": "answer_completed", "modelConfigId": model.id,
                        "responseId": response_id, "status": "failed" if error_code else "success", "errorCode": error_code})
            finally:
                if not cancelled:
                    await queue.put({"type": "branch_done"})
        if cancelled:
            raise asyncio.CancelledError

    async def _save(self, conversation_id: int, owner: int, response_id: int, model: RuntimeModelConfig,
                    reply: ModelReply | None, call_started: bool, error_code: str | None) -> None:
        """保存回答与生成费用，旧评分统计不接纳尚未评分的新多轮结果。"""
        async with self.sessions() as db:
            conversation = await self.store.get(db, conversation_id, owner, owner_only=True, lock=True)
            if conversation.generation_status != "generating":
                return
            response = await db.scalar(select(ModelResponse).where(ModelResponse.id == response_id).with_for_update())
            if response is None or response.status != "pending":
                return
            turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.task_id == response.task_id,
                ConversationTurn.conversation_id == conversation_id))
            if turn is None or turn.turn_index != conversation.current_turn or turn.generation_status != "generating":
                return
            response.status = "failed" if error_code else "success"
            response.answer_text = reply.answer if reply and not error_code else ""
            response.error_message = "本分支生成失败或中断" if error_code else None
            known = reply is not None and reply.usage_known
            if known:
                costs = estimate_stage_cost(model_snapshot(model), reply.usage)
                for key, value in asdict(reply.usage).items():
                    setattr(response, key, value)
                for key, value in asdict(costs).items():
                    setattr(response, key, value)
                response.total_tokens = reply.usage.total_tokens
                response.estimated_cost = costs.total_cost
            if reply:
                response.latency_ms = reply.latency_ms
            db.add(EvaluationResult(response_id=response_id, final_score=None,
                score_status="model_failed" if error_code else "judge_disabled", excluded_from_stats=True))
            if call_started:
                usage = await db.scalar(select(ConversationUsage).where(
                    ConversationUsage.conversation_id == conversation_id,
                    ConversationUsage.operation_key == f"generate:{response_id}").with_for_update())
                usage.status = "completed" if known else "unknown"
                usage.total_tokens = reply.usage.total_tokens if known else None
                usage.cost = response.estimated_cost if known else None
                usage.detail_json = {**usage.detail_json, "usage": asdict(reply.usage) if known else None}
                await db.flush()
                await token_quota_service.record_conversation_usage(db, usage_id=usage.id, user_id=owner)
            await db.commit()
