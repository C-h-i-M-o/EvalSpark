import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import ModelRequest
from app.adapters.openai_compatible import OpenAICompatibleClient
from app.core.config import settings
from app.models.comment import UserComment
from app.models.evaluation import EvaluationResult, EvaluationTask
from app.models.feedback import UserFeedback
from app.models.model_config import ModelConfig
from app.models.response import ModelResponse
from app.models.user import User
from app.schemas.evaluation import (
    CommentCreate,
    CommentListRead,
    CommentRead,
    EvaluationFeedbackRead,
    ModelCostDetailsRead,
    EvaluationScoreRead,
    EvaluationTaskCreate,
    EvaluationTaskListItemRead,
    EvaluationTaskListRead,
    EvaluationTaskRead,
    FeedbackCreate,
    FeedbackToggleRead,
    JudgeRunRead,
    ModelResponseRead,
)
from app.services.llm_judge_evaluator import LLMJudgeResult, llm_judge_evaluator
from app.services.model_config_service import RuntimeModelConfig, model_config_service
from app.services.rule_evaluator import rule_evaluator
from app.services.token_quota_service import token_quota_service
from app.services.rag.service import RagRun, rag_evaluation_service
from app.services.rag.scoring import serialize_rag_detail, serialize_rag_score
from app.services.rag.judge import apply_rag_feedback


BUILTIN_SYSTEM_PROMPT = """你是一个严谨、清晰、负责任的 AI 助手。请基于用户问题直接作答，并遵守以下要求：

1. 优先回答用户真正的问题，不要回避核心诉求。
2. 保持中文表达清晰、自然、结构化；如果用户明确要求其他语言，则按用户要求回复。
3. 如果问题适合分步骤、分点或对比说明，请使用清晰的段落、列表或表格组织答案。
4. 如果用户要求代码、JSON、表格、步骤、方案或对比，请严格遵守对应格式。
5. 不要编造不确定的信息。遇到无法确认的事实、数据、时间、版本或来源时，请明确说明不确定性。
6. 对涉及医疗、法律、金融、安全等高风险内容的问题，请给出谨慎、一般性的信息，并提醒用户寻求专业意见。
7. 避免输出违法、有害、危险操作指导、隐私泄露、凭据泄露或恶意攻击相关内容。
8. 回答应兼顾完整性和简洁性：必要时解释原因、给出示例或注意事项，但不要无意义冗长。
9. 如果用户问题本身含糊，请先基于最合理的理解回答，并指出关键假设；不要反复追问导致无法推进。

用户问题如下："""

FEEDBACK_TYPES = ("like", "dislike")
JUDGE_PROMPT_CODES = ("judge_v1_a", "judge_v1_b", "judge_v1_c")
JUDGE_MIN_SUCCESSFUL_RUNS = 2
JUDGE_STABILITY_THRESHOLD = 2.0


class EvaluationTaskNotFoundError(Exception):
    pass


class EvaluationTaskValidationError(Exception):
    pass


class EvaluationResponseNotFoundError(Exception):
    pass


class EvaluationCommentNotFoundError(Exception):
    pass


class EvaluationService:
    async def create_task(
        self,
        payload: EvaluationTaskCreate,
        db: AsyncSession,
        user_id: int,
        username: str,
    ) -> EvaluationTaskRead:
        if payload.task_type == "rag":
            run = await rag_evaluation_service.start(payload, db, user_id)
            async with aclosing(rag_evaluation_service.stream(run, self)) as events:
                async for event in events:
                    if event["type"] == "task_completed" and isinstance(event["task"], EvaluationTaskRead):
                        return event["task"]
            return await rag_evaluation_service.read_task(run, self)
        selected_models = await model_config_service.resolve_runtime_models(db, payload.model_ids)
        self._ensure_judge_model_is_idle(payload, selected_models)
        judge_model = await self._resolve_judge_model(db, payload)
        task_id = await self._create_task_record(db, payload, user_id)
        responses = await self._collect_model_responses(
            db=db,
            task_id=task_id,
            prompt=payload.prompt,
            models=selected_models,
            extra_body=self._thinking_extra_body(payload),
            judge_model=judge_model,
            judge_enabled=payload.enable_judge,
            user_id=user_id,
        )
        task_status = "completed" if any(response.status == "success" for response in responses) else "failed"
        await self._finish_task_record(db, task_id, task_status)

        return EvaluationTaskRead(
            taskId=task_id,
            status=task_status,
            prompt=payload.prompt,
            ownerId=user_id,
            ownerUsername=username,
            visibility=payload.visibility,
            responses=responses,
        )

    async def stream_task_events(
        self,
        payload: EvaluationTaskCreate,
        db: AsyncSession,
        user_id: int,
        username: str,
        rag_run: RagRun | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        if payload.task_type == "rag":
            run = rag_run or await rag_evaluation_service.start(payload, db, user_id)
            async with aclosing(rag_evaluation_service.stream(run, self)) as events:
                async for event in events:
                    yield event
            return
        selected_models = await model_config_service.resolve_runtime_models(db, payload.model_ids)
        self._ensure_judge_model_is_idle(payload, selected_models)
        judge_model = await self._resolve_judge_model(db, payload)
        task_id = await self._create_task_record(db, payload, user_id)
        model_ids = [model.id for model in selected_models]
        extra_body = self._thinking_extra_body(payload)
        responses: list[ModelResponseRead] = []

        yield {
            "type": "task_started",
            "taskType": "chat",
            "taskId": task_id,
            "prompt": payload.prompt,
            "modelIds": model_ids,
            "total": len(selected_models),
        }

        event_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        tasks = [
            asyncio.create_task(
                self._stream_model_worker(
                    queue=event_queue,
                    prompt=payload.prompt,
                    model=model,
                    extra_body=extra_body,
                )
            )
            for model in selected_models
        ]
        scoring_tasks: list[asyncio.Task[None]] = []
        completed_models = 0
        try:
            while completed_models < len(tasks):
                event = await event_queue.get()
                if event["type"] == "response_ready":
                    response = event["response"]
                    if not isinstance(response, ModelResponseRead):
                        continue
                    scoring_tasks.append(
                        asyncio.create_task(
                            self._score_model_worker(
                                queue=event_queue,
                                prompt=payload.prompt,
                                response=response,
                                judge_model=judge_model,
                                judge_enabled=payload.enable_judge,
                            )
                        )
                    )
                    continue
                if event["type"] == "scored_response_ready":
                    completed_models += 1
                    response = event["response"]
                    if not isinstance(response, ModelResponseRead):
                        continue
                    persisted_response = await self._persist_response(db, task_id, response, user_id)
                    responses.append(persisted_response)
                    yield {"type": "model_response", "response": persisted_response}
                    continue
                yield event
        finally:
            for task in [*tasks, *scoring_tasks]:
                if not task.done():
                    task.cancel()

        task_status = "completed" if any(response.status == "success" for response in responses) else "failed"
        await self._finish_task_record(db, task_id, task_status)
        yield {
            "type": "task_completed",
            "task": EvaluationTaskRead(
                taskId=task_id,
                status=task_status,
                prompt=payload.prompt,
                ownerId=user_id,
                ownerUsername=username,
                visibility=payload.visibility,
                responses=responses,
            ),
        }

    async def get_task(self, task_id: int, db: AsyncSession, user_id: int) -> EvaluationTaskRead:
        result = await db.execute(
            self._task_detail_query().where(
                EvaluationTask.id == task_id,
                self._task_access_condition(user_id),
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise EvaluationTaskNotFoundError("评测任务不存在")

        return self._serialize_task(task, user_id)

    async def toggle_response_feedback(
        self,
        response_id: int,
        payload: FeedbackCreate,
        db: AsyncSession,
        user_id: int,
    ) -> FeedbackToggleRead:
        response_result = await db.execute(
            select(ModelResponse)
            .join(EvaluationTask, EvaluationTask.id == ModelResponse.task_id)
            .options(
                selectinload(ModelResponse.task),
                selectinload(ModelResponse.evaluation_result),
                selectinload(ModelResponse.rag_detail),
            )
            .where(
                ModelResponse.id == response_id,
                self._task_access_condition(user_id),
            )
        )
        response_record = response_result.scalar_one_or_none()
        if response_record is None:
            raise EvaluationResponseNotFoundError("模型回答不存在")

        if getattr(response_record.task, "task_type", "chat") == "rag":
            task_id = response_record.task_id
            await db.rollback()
            # 与评分写入保持“先任务、后回答”的锁顺序，防止反馈与终态更新互相覆盖。
            await db.execute(select(EvaluationTask.id).where(EvaluationTask.id == task_id,
                EvaluationTask.user_id == user_id).with_for_update())
            response_record = (await db.execute(select(ModelResponse).where(ModelResponse.id == response_id)
                .options(selectinload(ModelResponse.task), selectinload(ModelResponse.evaluation_result),
                         selectinload(ModelResponse.rag_detail))
                .with_for_update().execution_options(populate_existing=True))).scalar_one()

        feedback_result = await db.execute(
            select(UserFeedback)
            .where(
                UserFeedback.response_id == response_id,
                UserFeedback.user_id == user_id,
                UserFeedback.feedback_type.in_(FEEDBACK_TYPES),
            )
            .order_by(UserFeedback.id.asc())
        )
        current_feedback = list(feedback_result.scalars().all())
        selected_feedback = next(
            (feedback for feedback in current_feedback if feedback.feedback_type == payload.feedback_type),
            None,
        )

        active = True
        if selected_feedback is not None:
            for feedback in current_feedback:
                await db.delete(feedback)
            active = False
        elif current_feedback:
            primary_feedback = current_feedback[0]
            primary_feedback.feedback_type = payload.feedback_type
            for feedback in current_feedback[1:]:
                await db.delete(feedback)
        else:
            db.add(
                UserFeedback(
                    user_id=user_id,
                    response_id=response_id,
                    feedback_type=payload.feedback_type,
                )
            )

        await db.flush()
        feedback = await self._read_response_feedback(db, response_id, user_id)
        score = self._recalculate_final_score(response_record, feedback)
        await db.commit()
        return FeedbackToggleRead(
            responseId=response_id,
            feedbackType=payload.feedback_type,
            active=active,
            feedback=feedback,
            score=score,
        )

    async def list_response_comments(
        self,
        response_id: int,
        db: AsyncSession,
        page: int,
        page_size: int,
        user_id: int,
    ) -> CommentListRead:
        await self._require_response_access(db, response_id, user_id)
        normalized_page = max(page, 1)
        normalized_page_size = min(max(page_size, 1), 100)
        offset = (normalized_page - 1) * normalized_page_size

        total_result = await db.execute(
            select(func.count(UserComment.id)).where(UserComment.response_id == response_id)
        )
        total = int(total_result.scalar_one())
        comments_result = await db.execute(
            select(UserComment, User.username)
            .join(User, User.id == UserComment.user_id)
            .where(UserComment.response_id == response_id)
            .order_by(UserComment.created_at.desc(), UserComment.id.desc())
            .offset(offset)
            .limit(normalized_page_size)
        )
        return CommentListRead(
            items=[
                self._serialize_comment(comment, username, user_id)
                for comment, username in comments_result.all()
            ],
            total=total,
            page=normalized_page,
            pageSize=normalized_page_size,
        )

    async def create_response_comment(
        self,
        response_id: int,
        payload: CommentCreate,
        db: AsyncSession,
        user_id: int,
        username: str,
    ) -> CommentRead:
        await self._require_response_access(db, response_id, user_id)
        comment = UserComment(
            user_id=user_id,
            response_id=response_id,
            content=payload.content,
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)
        return self._serialize_comment(comment, username, user_id)

    async def delete_response_comment(self, comment_id: int, db: AsyncSession, user_id: int) -> None:
        result = await db.execute(
            select(UserComment).join(ModelResponse, ModelResponse.id == UserComment.response_id)
            .join(EvaluationTask, EvaluationTask.id == ModelResponse.task_id).where(
                UserComment.id == comment_id,
                UserComment.user_id == user_id,
                or_(EvaluationTask.task_type == "chat", EvaluationTask.user_id == user_id),
            )
        )
        comment = result.scalar_one_or_none()
        if comment is None:
            raise EvaluationCommentNotFoundError("评论不存在")
        await db.delete(comment)
        await db.commit()

    async def list_tasks(
        self,
        db: AsyncSession,
        page: int,
        page_size: int,
        user_id: int,
        task_type: str | None = None,
    ) -> EvaluationTaskListRead:
        normalized_page = max(page, 1)
        normalized_page_size = min(max(page_size, 1), 100)
        offset = (normalized_page - 1) * normalized_page_size

        filters = [self._task_access_condition(user_id)]
        if task_type is not None:
            filters.append(EvaluationTask.task_type == task_type)

        total_result = await db.execute(
            select(func.count(EvaluationTask.id)).where(*filters)
        )
        total = int(total_result.scalar_one())
        rows_result = await db.execute(
            select(EvaluationTask, func.count(ModelResponse.id))
            .options(selectinload(EvaluationTask.user))
            .outerjoin(ModelResponse, ModelResponse.task_id == EvaluationTask.id)
            .where(*filters)
            .group_by(EvaluationTask.id)
            .order_by(EvaluationTask.created_at.desc(), EvaluationTask.id.desc())
            .offset(offset)
            .limit(normalized_page_size)
        )

        items = [
            EvaluationTaskListItemRead(
                taskType=getattr(task, "task_type", None) or "chat",
                taskId=task.id,
                status=task.status,
                prompt=task.prompt,
                createdAt=task.created_at,
                completedAt=task.completed_at,
                responseCount=int(response_count),
                ownerId=task.user_id,
                ownerUsername=task.user.username if task.user is not None else "anonymous",
                visibility=task.visibility,
            )
            for task, response_count in rows_result.all()
        ]

        return EvaluationTaskListRead(
            items=items,
            total=total,
            page=normalized_page,
            pageSize=normalized_page_size,
        )

    async def _collect_model_responses(
        self,
        db: AsyncSession,
        task_id: int,
        prompt: str,
        models: list[RuntimeModelConfig],
        extra_body: dict[str, object],
        judge_model: RuntimeModelConfig | None,
        judge_enabled: bool,
        user_id: int,
    ) -> list[ModelResponseRead]:
        tasks = [self._call_model(prompt=prompt, model=model, extra_body=extra_body) for model in models]
        responses = await asyncio.gather(*tasks)
        persisted_responses = []
        for response in responses:
            response = await self._apply_judge_score(prompt, response, judge_model, judge_enabled)
            persisted_responses.append(await self._persist_response(db, task_id, response, user_id))
        return persisted_responses

    async def _call_model(
        self,
        prompt: str,
        model: RuntimeModelConfig,
        extra_body: dict[str, object],
    ) -> ModelResponseRead:
        client = OpenAICompatibleClient(
            model_name=model.model_name,
            base_url=model.base_url,
            api_key=model.api_key,
            input_price=model.input_price,
            output_price=model.output_price,
            cache_hit_price=model.cache_hit_price,
            cache_creation_price=model.cache_creation_price,
            timeout=model.timeout_seconds,
            extra_body=model.extra_body,
        )

        try:
            reply = await asyncio.wait_for(
                client.chat(
                    ModelRequest(
                        prompt=self._model_prompt(prompt),
                        model_name=model.model_name,
                        max_tokens=model.max_tokens,
                        temperature=model.temperature,
                        extra_body=extra_body,
                    )
                ),
                timeout=model.timeout_seconds + 5,
            )
            cost_details = client.estimate_cost_details(reply.usage)
            estimated_cost = float(cost_details.total_cost)
            score = rule_evaluator.evaluate(prompt=prompt, answer=reply.answer)

            return ModelResponseRead(
                id=model.id,
                modelConfigId=model.id,
                modelName=model.display_name,
                provider=model.provider_name,
                answer=reply.answer,
                latencyMs=reply.latency_ms,
                inputTokens=reply.usage.input_tokens,
                outputTokens=reply.usage.output_tokens,
                cacheHitTokens=reply.usage.cache_hit_tokens,
                cacheCreationTokens=reply.usage.cache_creation_tokens,
                totalTokens=reply.usage.total_tokens,
                estimatedCost=estimated_cost,
                currency=model.currency,
                costDetails=ModelCostDetailsRead(
                    inputCost=float(cost_details.input_cost),
                    outputCost=float(cost_details.output_cost),
                    cacheHitCost=float(cost_details.cache_hit_cost),
                    cacheCreationCost=float(cost_details.cache_creation_cost),
                ),
                configSnapshot=self._config_snapshot(model),
                status="success",
                score=EvaluationScoreRead(**score),
            )
        except TimeoutError:
            return self._failed_model_response(
                model,
                f"模型调用超时：{model.display_name} 超过 {model.timeout_seconds} 秒未返回",
            )
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as error:
            return self._failed_model_response(model, f"模型调用失败：{error}")

    async def _stream_model_worker(
        self,
        queue: asyncio.Queue[dict[str, object]],
        prompt: str,
        model: RuntimeModelConfig,
        extra_body: dict[str, object],
    ) -> None:
        try:
            async for event in self._stream_model_response(prompt=prompt, model=model, extra_body=extra_body):
                if event["type"] == "delta":
                    queue.put_nowait(
                        {
                            "type": "model_delta",
                            "modelConfigId": model.id,
                            "delta": event["delta"],
                        }
                    )
                    continue
                if event["type"] == "response":
                    queue.put_nowait({"type": "model_answer_completed", "modelConfigId": model.id})
                    queue.put_nowait({"type": "response_ready", "response": event["response"]})
        except Exception as error:
            queue.put_nowait(
                {
                    "type": "response_ready",
                    "response": self._failed_model_response(model, f"模型调用失败：{error}"),
                }
            )

    async def _score_model_worker(
        self,
        queue: asyncio.Queue[dict[str, object]],
        prompt: str,
        response: ModelResponseRead,
        judge_model: RuntimeModelConfig | None,
        judge_enabled: bool,
    ) -> None:
        try:
            scored_response = await self._apply_judge_score(prompt, response, judge_model, judge_enabled)
        except Exception:
            scored_response = response
        queue.put_nowait({"type": "scored_response_ready", "response": scored_response})

    async def _stream_model_response(
        self,
        prompt: str,
        model: RuntimeModelConfig,
        extra_body: dict[str, object],
    ) -> AsyncIterator[dict[str, object]]:
        client = OpenAICompatibleClient(
            model_name=model.model_name,
            base_url=model.base_url,
            api_key=model.api_key,
            input_price=model.input_price,
            output_price=model.output_price,
            cache_hit_price=model.cache_hit_price,
            cache_creation_price=model.cache_creation_price,
            timeout=model.timeout_seconds,
            extra_body=model.extra_body,
        )
        try:
            reply = None
            async with asyncio.timeout(model.timeout_seconds + 5):
                async for event in client.stream_chat(
                    ModelRequest(
                        prompt=self._model_prompt(prompt),
                        model_name=model.model_name,
                        max_tokens=model.max_tokens,
                        temperature=model.temperature,
                        extra_body=extra_body,
                    )
                ):
                    if event.delta:
                        yield {"type": "delta", "delta": event.delta}
                    if event.reply is not None:
                        reply = event.reply

            if reply is None:
                raise ValueError("模型流式响应未返回完整结果")

            cost_details = client.estimate_cost_details(reply.usage)
            estimated_cost = float(cost_details.total_cost)
            score = rule_evaluator.evaluate(prompt=prompt, answer=reply.answer)
            yield {
                "type": "response",
                "response": ModelResponseRead(
                    id=model.id,
                    modelConfigId=model.id,
                    modelName=model.display_name,
                    provider=model.provider_name,
                    answer=reply.answer,
                    latencyMs=reply.latency_ms,
                    inputTokens=reply.usage.input_tokens,
                    outputTokens=reply.usage.output_tokens,
                    cacheHitTokens=reply.usage.cache_hit_tokens,
                    cacheCreationTokens=reply.usage.cache_creation_tokens,
                    totalTokens=reply.usage.total_tokens,
                    estimatedCost=estimated_cost,
                    currency=model.currency,
                    costDetails=ModelCostDetailsRead(
                        inputCost=float(cost_details.input_cost),
                        outputCost=float(cost_details.output_cost),
                        cacheHitCost=float(cost_details.cache_hit_cost),
                        cacheCreationCost=float(cost_details.cache_creation_cost),
                    ),
                    configSnapshot=self._config_snapshot(model),
                    status="success",
                    score=EvaluationScoreRead(**score),
                ),
            }
        except TimeoutError:
            yield {"type": "response", "response": self._failed_model_response(model, "模型调用超时")}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
            yield {"type": "response", "response": self._failed_model_response(model, f"模型调用失败：{error}")}

    def _failed_model_response(self, model: RuntimeModelConfig, answer: str) -> ModelResponseRead:
        score = rule_evaluator.evaluate(prompt="", answer="")
        score.update(
            {
                "final": None,
                "ruleFinal": score.get("ruleFinal", 0),
                "baseFinal": None,
                "judgeFinal": None,
                "scoreStatus": "model_failed",
                "excludedFromStats": True,
            }
        )
        return ModelResponseRead(
            id=model.id,
            modelConfigId=model.id,
            modelName=model.display_name,
            provider=model.provider_name,
            answer=answer,
            latencyMs=0,
            inputTokens=0,
            outputTokens=0,
            cacheHitTokens=0,
            cacheCreationTokens=0,
            totalTokens=0,
            estimatedCost=0,
            currency=model.currency,
            costDetails=ModelCostDetailsRead(),
            configSnapshot=self._config_snapshot(model),
            status="failed",
            score=EvaluationScoreRead(**score),
        )

    async def _resolve_judge_model(
        self,
        db: AsyncSession,
        payload: EvaluationTaskCreate,
    ) -> RuntimeModelConfig | None:
        if not payload.enable_judge or payload.judge_model_id is None:
            return None
        return await model_config_service.resolve_runtime_model(db, payload.judge_model_id)

    async def validate_task_models(self, payload: EvaluationTaskCreate, db: AsyncSession) -> None:
        selected_models = await model_config_service.resolve_runtime_models(db, payload.model_ids)
        self._ensure_judge_model_is_idle(payload, selected_models)

    def _ensure_judge_model_is_idle(
        self,
        payload: EvaluationTaskCreate,
        selected_models: list[RuntimeModelConfig],
    ) -> None:
        if not payload.enable_judge or payload.judge_model_id is None:
            return
        selected_model_ids = {model.id for model in selected_models}
        if payload.judge_model_id in selected_model_ids:
            raise EvaluationTaskValidationError("LLM 评审模型不能同时作为被测模型")

    async def _apply_judge_score(
        self,
        prompt: str,
        response: ModelResponseRead,
        judge_model: RuntimeModelConfig | None,
        judge_enabled: bool = True,
    ) -> ModelResponseRead:
        if response.status != "success":
            return response
        if not judge_enabled:
            return response.model_copy(update={"score": self._mark_judge_disabled(response.score)})
        if judge_model is None:
            failed = LLMJudgeResult(score=None, comment="LLM 评审失败：没有可用的评审模型", details={})
            return response.model_copy(update={"score": self._merge_judge_runs(response.score, [failed])})

        judge_results = await asyncio.gather(
            *[
                llm_judge_evaluator.evaluate(
                    prompt=prompt,
                    answer=response.answer,
                    model=judge_model,
                    prompt_code=prompt_code,
                )
                for prompt_code in JUDGE_PROMPT_CODES
            ]
        )
        return response.model_copy(update={"score": self._merge_judge_runs(response.score, list(judge_results))})

    def _merge_judge_score(self, score: EvaluationScoreRead, judge_result: LLMJudgeResult) -> EvaluationScoreRead:
        return self._merge_judge_runs(score, [judge_result])

    def _merge_judge_runs(
        self,
        score: EvaluationScoreRead,
        judge_results: list[LLMJudgeResult],
    ) -> EvaluationScoreRead:
        rule_final = score.rule_final if score.rule_final is not None else score.final
        judge_runs = [
            JudgeRunRead(
                runIndex=index + 1,
                promptCode=JUDGE_PROMPT_CODES[index] if index < len(JUDGE_PROMPT_CODES) else f"judge_v1_{index + 1}",
                score=round(result.score, 2) if result.score is not None else None,
                confidence=None,
                comment=result.comment if result.score is not None else None,
                error=result.comment if result.score is None else None,
            )
            for index, result in enumerate(judge_results)
        ]
        successful_scores = [run.score for run in judge_runs if run.score is not None]
        score_range = round(max(successful_scores) - min(successful_scores), 2) if successful_scores else None
        if len(successful_scores) < JUDGE_MIN_SUCCESSFUL_RUNS:
            return score.model_copy(
                update={
                    "final": None,
                    "rule_final": round(rule_final, 2) if rule_final is not None else None,
                    "judge_final": None,
                    "base_final": None,
                    "judge_comment": "LLM 评审失败，本次不计入统计",
                    "judge_details": {},
                    "score_status": "judge_failed",
                    "excluded_from_stats": True,
                    "judge_runs": judge_runs,
                    "judge_score_range": score_range,
                }
            )
        if score_range is not None and score_range > JUDGE_STABILITY_THRESHOLD:
            return score.model_copy(
                update={
                    "final": None,
                    "rule_final": round(rule_final, 2) if rule_final is not None else None,
                    "judge_final": None,
                    "base_final": None,
                    "judge_comment": "LLM 三次评分分歧较大，本次不计入统计",
                    "judge_details": {},
                    "score_status": "judge_unstable",
                    "excluded_from_stats": True,
                    "judge_runs": judge_runs,
                    "judge_score_range": score_range,
                }
            )
        if rule_final is None:
            return score
        judge_final = round(sum(successful_scores) / len(successful_scores), 2)
        final = Decimal(str(rule_final)) * Decimal("0.30") + Decimal(str(judge_final)) * Decimal("0.70")
        return score.model_copy(
            update={
                "final": float(round(final, 2)),
                "rule_final": round(rule_final, 2),
                "judge_final": judge_final,
                "base_final": float(round(final, 2)),
                "judge_comment": f"LLM {len(successful_scores)} 次有效评分稳定，采用平均分",
                "judge_details": {},
                "score_status": "scored",
                "excluded_from_stats": False,
                "judge_runs": judge_runs,
                "judge_score_range": score_range,
            }
        )

    def _mark_judge_disabled(self, score: EvaluationScoreRead) -> EvaluationScoreRead:
        rule_final = score.rule_final if score.rule_final is not None else score.final
        return score.model_copy(
            update={
                "final": None,
                "rule_final": round(rule_final, 2) if rule_final is not None else None,
                "judge_final": None,
                "base_final": None,
                "judge_comment": "本次关闭 LLM 评分，仅展示规则检查，不计入统计",
                "judge_details": {},
                "score_status": "judge_disabled",
                "excluded_from_stats": True,
                "judge_runs": [],
                "judge_score_range": None,
            }
        )

    def _thinking_extra_body(self, payload: EvaluationTaskCreate) -> dict[str, object]:
        thinking_type = "enabled" if payload.enable_thinking else "disabled"
        return {"thinking": {"type": thinking_type}}

    def _model_prompt(self, prompt: str) -> str:
        return f"{BUILTIN_SYSTEM_PROMPT}\n\n{prompt.strip()}"

    async def _create_task_record(
        self,
        db: AsyncSession,
        payload: EvaluationTaskCreate,
        user_id: int,
    ) -> int:
        task = EvaluationTask(
            conversation_id=payload.conversation_id,
            user_id=user_id,
            prompt=payload.prompt,
            status="pending",
            visibility=payload.visibility,
        )
        db.add(task)
        await db.commit()
        return task.id

    async def _persist_response(
        self,
        db: AsyncSession,
        task_id: int,
        response: ModelResponseRead,
        user_id: int,
    ) -> ModelResponseRead:
        answer_text = response.answer if response.status == "success" else ""
        error_message = None if response.status == "success" else response.answer
        response_record = ModelResponse(
            task_id=task_id,
            model_config_id=response.model_config_id,
            answer_text=answer_text,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_hit_tokens=response.cache_hit_tokens,
            cache_creation_tokens=response.cache_creation_tokens,
            total_tokens=response.total_tokens,
            input_cost=Decimal(str(response.cost_details.input_cost)),
            output_cost=Decimal(str(response.cost_details.output_cost)),
            cache_hit_cost=Decimal(str(response.cost_details.cache_hit_cost)),
            cache_creation_cost=Decimal(str(response.cost_details.cache_creation_cost)),
            estimated_cost=Decimal(str(response.estimated_cost)),
            currency=response.currency,
            config_snapshot=response.config_snapshot,
            status=response.status,
            error_message=error_message,
        )
        db.add(response_record)
        await db.flush()
        await token_quota_service.record_usage(
            db,
            user_id=user_id,
            task_id=task_id,
            response_id=response_record.id,
            model_config_id=response.model_config_id,
            total_tokens=response.total_tokens,
        )

        score = response.score
        judge_comment = self._encode_judge_comment(score.judge_comment, score.judge_details)
        db.add(
            EvaluationResult(
                response_id=response_record.id,
                relevance_score=Decimal(str(score.relevance)),
                completeness_score=Decimal(str(score.completeness)),
                clarity_score=Decimal(str(score.clarity)),
                format_score=Decimal(str(score.format)),
                safety_score=Decimal(str(score.safety)),
                rule_score=Decimal(str(score.rule_final if score.rule_final is not None else score.final)),
                judge_score=Decimal(str(score.judge_final)) if score.judge_final is not None else None,
                final_score=Decimal(str(score.final)) if score.final is not None else None,
                score_status=score.score_status,
                excluded_from_stats=score.excluded_from_stats,
                judge_score_range=(
                    Decimal(str(score.judge_score_range)) if score.judge_score_range is not None else None
                ),
                judge_runs_json=[run.model_dump(by_alias=True) for run in score.judge_runs],
                rule_dictionary_version=score.rule_dictionary_version,
                judge_comment=judge_comment,
            )
        )
        await db.commit()

        return ModelResponseRead(
            id=response_record.id,
            modelConfigId=response.model_config_id,
            modelName=response.model_name,
            provider=response.provider,
            answer=response.answer,
            latencyMs=response.latency_ms,
            inputTokens=response.input_tokens,
            outputTokens=response.output_tokens,
            cacheHitTokens=response.cache_hit_tokens,
            cacheCreationTokens=response.cache_creation_tokens,
            totalTokens=response.total_tokens,
            estimatedCost=response.estimated_cost,
            currency=response.currency,
            costDetails=response.cost_details,
            status=response.status,
            score=response.score,
            feedback=EvaluationFeedbackRead(),
        )

    async def _finish_task_record(self, db: AsyncSession, task_id: int, status: str) -> None:
        result = await db.execute(select(EvaluationTask).where(EvaluationTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            raise EvaluationTaskNotFoundError("评测任务不存在")

        task.status = status
        task.completed_at = datetime.utcnow()
        await db.commit()

    def _task_detail_query(self) -> Select[tuple[EvaluationTask]]:
        return select(EvaluationTask).options(
            selectinload(EvaluationTask.responses)
            .selectinload(ModelResponse.model_config)
            .selectinload(ModelConfig.provider),
            selectinload(EvaluationTask.responses).selectinload(ModelResponse.evaluation_result),
            selectinload(EvaluationTask.responses).selectinload(ModelResponse.feedback),
            selectinload(EvaluationTask.responses).selectinload(ModelResponse.rag_detail),
            selectinload(EvaluationTask.user),
        )

    def _serialize_task(self, task: EvaluationTask, user_id: int) -> EvaluationTaskRead:
        responses = sorted(task.responses, key=lambda response: (response.created_at, response.id))
        return EvaluationTaskRead(
            taskType=getattr(task, "task_type", None) or "chat",
            taskId=task.id,
            status=task.status,
            prompt=task.prompt,
            createdAt=task.created_at,
            completedAt=task.completed_at,
            ownerId=task.user_id,
            ownerUsername=task.user.username if task.user is not None else "anonymous",
            visibility=task.visibility,
            responses=[self._serialize_response(task.prompt, response, user_id) for response in responses],
        )

    def _serialize_response(self, prompt: str, response: ModelResponse, user_id: int) -> ModelResponseRead:
        model_config = response.model_config
        provider = model_config.provider if model_config is not None else None
        snapshot = response.config_snapshot or {}
        answer = response.answer_text if response.status == "success" else response.error_message or response.answer_text
        feedback = self._serialize_feedback(response.feedback, user_id)
        rag_detail = getattr(response, "rag_detail", None)
        if rag_detail is not None:
            answer = response.answer_text
            score = serialize_rag_score(response.evaluation_result, rag_detail, prompt, answer, feedback)
        else:
            score = self._serialize_score(response.evaluation_result, prompt=prompt,
                answer=response.answer_text, feedback=feedback)

        return ModelResponseRead(
            id=response.id,
            rag=serialize_rag_detail(rag_detail) if rag_detail is not None else None,
            modelConfigId=response.model_config_id,
            modelName=(
                model_config.display_name
                if model_config is not None
                else str(snapshot.get("displayName") or snapshot.get("modelName") or "未知模型")
            ),
            provider=(
                provider.name
                if provider is not None
                else str(snapshot.get("providerName") or "unknown")
            ),
            answer=answer,
            latencyMs=response.latency_ms,
            inputTokens=response.input_tokens,
            outputTokens=response.output_tokens,
            cacheHitTokens=response.cache_hit_tokens,
            cacheCreationTokens=response.cache_creation_tokens,
            totalTokens=response.total_tokens,
            estimatedCost=float(response.estimated_cost),
            currency=response.currency,
            costDetails=ModelCostDetailsRead(
                inputCost=float(response.input_cost),
                outputCost=float(response.output_cost),
                cacheHitCost=float(response.cache_hit_cost),
                cacheCreationCost=float(response.cache_creation_cost),
            ),
            status=response.status,
            score=score,
            feedback=feedback,
        )

    def _config_snapshot(self, model: RuntimeModelConfig) -> dict[str, object]:
        return {
            "providerName": model.provider_name,
            "displayName": model.display_name,
            "modelName": model.model_name,
            "baseUrl": model.base_url,
            "maxTokens": model.max_tokens,
            "temperature": model.temperature,
            "timeoutSeconds": model.timeout_seconds,
            "notes": model.notes,
            "currency": model.currency,
            "priceInput": str(model.input_price),
            "priceOutput": str(model.output_price),
            "priceCacheHit": str(model.cache_hit_price),
            "priceCacheCreation": str(model.cache_creation_price),
        }

    async def _read_response_feedback(
        self,
        db: AsyncSession,
        response_id: int,
        user_id: int,
    ) -> EvaluationFeedbackRead:
        result = await db.execute(select(UserFeedback).where(UserFeedback.response_id == response_id))
        return self._serialize_feedback(list(result.scalars().all()), user_id)

    def _serialize_feedback(
        self,
        feedback_records: list[UserFeedback],
        user_id: int,
    ) -> EvaluationFeedbackRead:
        like_count = sum(1 for feedback in feedback_records if feedback.feedback_type == "like")
        dislike_count = sum(1 for feedback in feedback_records if feedback.feedback_type == "dislike")
        return EvaluationFeedbackRead(
            liked=any(
                feedback.user_id == user_id and feedback.feedback_type == "like" for feedback in feedback_records
            ),
            disliked=any(
                feedback.user_id == user_id and feedback.feedback_type == "dislike" for feedback in feedback_records
            ),
            likeCount=like_count,
            dislikeCount=dislike_count,
        )

    def _serialize_score(
        self,
        result: EvaluationResult | None,
        prompt: str = "",
        answer: str = "",
        feedback: EvaluationFeedbackRead | None = None,
    ) -> EvaluationScoreRead:
        details = rule_evaluator.evaluate(prompt=prompt, answer=answer).get("details", {})
        if result is None:
            return EvaluationScoreRead(
                relevance=0,
                completeness=0,
                clarity=0,
                format=0,
                safety=0,
                final=None,
                details=details,
                ruleFinal=0,
                baseFinal=None,
                scoreStatus="model_failed",
                excludedFromStats=True,
            )

        judge_comment, judge_details = self._decode_judge_comment(result.judge_comment)
        base_final = self._base_final(result)
        feedback_score = self._feedback_score(feedback)

        return EvaluationScoreRead(
            relevance=float(result.relevance_score),
            completeness=float(result.completeness_score),
            clarity=float(result.clarity_score),
            format=float(result.format_score),
            safety=float(result.safety_score),
            final=float(result.final_score) if result.final_score is not None else None,
            details=details,
            ruleFinal=float(result.rule_score),
            judgeFinal=float(result.judge_score) if result.judge_score is not None else None,
            baseFinal=base_final,
            feedbackScore=feedback_score,
            judgeComment=judge_comment,
            judgeDetails=judge_details,
            scoreStatus=result.score_status or "scored",
            excludedFromStats=bool(result.excluded_from_stats),
            judgeRuns=result.judge_runs_json or [],
            judgeScoreRange=float(result.judge_score_range) if result.judge_score_range is not None else None,
            ruleDictionaryVersion=result.rule_dictionary_version,
        )

    def _recalculate_final_score(
        self,
        response: ModelResponse,
        feedback: EvaluationFeedbackRead,
    ) -> EvaluationScoreRead:
        result = response.evaluation_result
        rag_detail = getattr(response, "rag_detail", None)
        if rag_detail is not None:
            if result is not None and result.score_status == "scored" and not result.excluded_from_stats and rag_detail.base_final is not None:
                result.final_score = apply_rag_feedback(rag_detail.base_final, feedback.like_count, feedback.dislike_count)
            return serialize_rag_score(result, rag_detail, response.task.prompt, response.answer_text, feedback)
        if result is None:
            return self._serialize_score(None, prompt=response.task.prompt, answer=response.answer_text)

        base_final = self._base_final(result)
        if base_final is None:
            return self._serialize_score(
                result,
                prompt=response.task.prompt,
                answer=response.answer_text,
                feedback=feedback,
            )
        feedback_score = self._feedback_score(feedback)
        final = base_final
        if feedback_score is not None:
            final = round(base_final * 0.90 + feedback_score * 0.10, 2)
        result.final_score = Decimal(str(final))
        return self._serialize_score(
            result,
            prompt=response.task.prompt,
            answer=response.answer_text,
            feedback=feedback,
        )

    def _base_final(self, result: EvaluationResult) -> float | None:
        if (result.score_status or "scored") != "scored" or bool(result.excluded_from_stats):
            return None
        rule_final = Decimal(str(result.rule_score))
        if result.judge_score is None:
            return float(round(rule_final, 2))
        base_final = rule_final * Decimal("0.30") + Decimal(str(result.judge_score)) * Decimal("0.70")
        return float(round(base_final, 2))

    def _feedback_score(self, feedback: EvaluationFeedbackRead | None) -> float | None:
        if feedback is None:
            return None
        total = feedback.like_count + feedback.dislike_count
        if total == 0:
            return None
        return round(10 * feedback.like_count / total, 2)

    async def _require_response_access(
        self,
        db: AsyncSession,
        response_id: int,
        user_id: int,
    ) -> None:
        result = await db.execute(
            select(ModelResponse.id)
            .join(EvaluationTask, EvaluationTask.id == ModelResponse.task_id)
            .where(
                ModelResponse.id == response_id,
                self._task_access_condition(user_id),
            )
        )
        if result.scalar_one_or_none() is None:
            raise EvaluationResponseNotFoundError("模型回答不存在")

    def _serialize_comment(
        self,
        comment: UserComment,
        username: str,
        user_id: int,
    ) -> CommentRead:
        return CommentRead(
            id=comment.id,
            responseId=comment.response_id,
            userId=comment.user_id,
            username=username,
            content=comment.content,
            createdAt=comment.created_at,
            canDelete=comment.user_id == user_id,
        )

    def _task_access_condition(self, user_id: int) -> object:
        return or_(
            and_(EvaluationTask.visibility == "public", EvaluationTask.task_type == "chat"),
            EvaluationTask.user_id == user_id,
        )

    def _encode_judge_comment(
        self,
        judge_comment: str | None,
        judge_details: dict[str, list[str]],
    ) -> str | None:
        if judge_comment is None and not judge_details:
            return None
        return json.dumps({"comment": judge_comment, "details": judge_details}, ensure_ascii=False)

    def _decode_judge_comment(self, value: str | None) -> tuple[str | None, dict[str, list[str]]]:
        if not value:
            return None, {}
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return value, {}
        if not isinstance(data, dict):
            return value, {}

        comment = data.get("comment")
        details = data.get("details")
        normalized_details: dict[str, list[str]] = {}
        if isinstance(details, dict):
            for key, items in details.items():
                if isinstance(key, str) and isinstance(items, list):
                    normalized_details[key] = [str(item) for item in items]
        return comment if isinstance(comment, str) else None, normalized_details


evaluation_service = EvaluationService()
