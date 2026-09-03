import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.evaluation import EvaluationTaskCreate, EvaluationTaskRead
from app.schemas.rag import PreparedRagResponse, RagAnswerReadyEvent, RagStageEvent, RagStreamEvent, RagTaskContext
from app.services.model_config_service import RuntimeModelConfig, model_config_service
from app.services.rag.evaluation import RagEvaluationRunner
from app.services.rag.evaluation_store import RAG_EXECUTION_TIMEOUT_SECONDS, RagEvaluationStore
from app.services.rag.judge import run_rag_judge
from app.services.rag.errors import KnowledgeBaseError

if TYPE_CHECKING:
    from app.services.evaluation_service import EvaluationService

logger = logging.getLogger(__name__)


@dataclass
class RagRun:
    context: RagTaskContext
    prepared: list[PreparedRagResponse]
    models: list[RuntimeModelConfig]
    judge: RuntimeModelConfig


class RagEvaluationService:
    def __init__(self, store: RagEvaluationStore | None = None, runner: RagEvaluationRunner | None = None) -> None:
        self.store = store or RagEvaluationStore()
        self.runner = runner or RagEvaluationRunner(self.store)

    async def start(self, payload: EvaluationTaskCreate, db: AsyncSession, user_id: int) -> RagRun:
        if payload.task_type != "rag" or payload.knowledge_base_id is None or payload.judge_model_id is None or not payload.enable_judge:
            raise KnowledgeBaseError("rag_invalid_request", "RAG 请求缺少知识库或评审模型", 422)
        models = await model_config_service.resolve_runtime_models(db, payload.model_ids)
        judges = await model_config_service.resolve_runtime_models(db, [payload.judge_model_id])
        if {model.id for model in models} != set(payload.model_ids) or any(not model.api_key for model in models):
            raise KnowledgeBaseError("rag_models_unavailable", "候选模型不存在或已停用")
        if (len(judges) != 1 or judges[0].id != payload.judge_model_id or not judges[0].api_key
            or judges[0].id in {model.id for model in models}):
            raise KnowledgeBaseError("rag_judge_unavailable", "必须选择未参与本次评测的可用评审模型")
        # 结束鉴权/配置只读快照；数据库锁仅存在于后续专用短会话中。
        await db.rollback()
        context, prepared = await self.store.create(user_id, payload.knowledge_base_id, payload.prompt, models,
            enable_thinking=payload.enable_thinking, conversation_id=payload.conversation_id)
        return RagRun(context, prepared, models, judges[0])

    async def read_task(self, run: RagRun, evaluator: "EvaluationService") -> EvaluationTaskRead:
        async with self.store.sessions() as db:
            return await evaluator.get_task(run.context.task_id, db, run.context.user_id)

    async def stream(self, run: RagRun, evaluator: "EvaluationService") -> AsyncIterator[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue(maxsize=128)
        consumer_closed = False

        async def emit(event: RagStreamEvent) -> None:
            await queue.put(event.model_dump(mode="json", by_alias=True))

        async def score(item: PreparedRagResponse) -> None:
            if item.failure_stage is None:
                await emit(RagStageEvent(model_config_id=item.model_config_id, stage="judging"))
                answer = await self.store.saved_answer(run.context, item.response_id)
                await run_rag_judge(run.context, item, answer, run.judge, self.store)
            await self.store.finalize_response(run.context, item.response_id)
            task = await self.read_task(run, evaluator)
            response = next(value for value in task.responses if value.id == item.response_id)
            await queue.put({"type": "model_response", "response": response})

        async def produce() -> None:
            scoring: list[asyncio.Task[None]] = []
            try:
                async with asyncio.timeout(RAG_EXECUTION_TIMEOUT_SECONDS):
                    await queue.put({"type": "task_started", "taskType": "rag", "taskId": run.context.task_id,
                        "prompt": run.context.prompt, "modelIds": [model.id for model in run.models], "total": len(run.models)})
                    await self.runner.prepare_rag_snapshots(run.context, run.prepared, run.models, emit=emit)
                    by_response = {item.response_id: item for item in run.prepared}
                    async with aclosing(self.runner.stream_rag_answers(run.context, run.prepared, run.models)) as events:
                        async for event in events:
                            if isinstance(event, RagAnswerReadyEvent):
                                scoring.append(asyncio.create_task(score(by_response[event.response_id])))
                            else:
                                await emit(event)
                    await asyncio.gather(*scoring)
                    await self.store.finish_task(run.context)
                    await queue.put({"type": "task_completed", "task": await self.read_task(run, evaluator)})
            finally:
                for task in scoring:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*scoring, return_exceptions=True)
                try:
                    await self.store.interrupt(run.context)
                except Exception:
                    logger.warning("RAG 任务收尾未完成，待超期恢复检查，不重试外部模型调用")
                if not consumer_closed:
                    await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                yield event
            await producer
        finally:
            consumer_closed = True
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)


rag_evaluation_service = RagEvaluationService()
