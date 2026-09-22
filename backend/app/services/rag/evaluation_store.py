from decimal import Decimal
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import inspect, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.services.embedding_config_service import get_config, runtime_config
from app.db.session import AsyncSessionLocal
from app.adapters.base import ModelUsage
from app.models.evaluation import EvaluationResult, EvaluationTask
from app.models.feedback import UserFeedback
from app.models.conversation import Conversation, ConversationTurn
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.rag import RagResponseDetail
from app.models.response import ModelResponse
from app.schemas.rag import PreparedRagResponse, RagEvidence, RagJudgeRun, RagStageUsage, RagTaskContext
from app.schemas.evaluation import EvaluationScoreRead
from app.services.knowledge_base_service import require_owned_knowledge_base
from app.services.model_config_service import RuntimeModelConfig
from app.services.rag.clients import RagClientError, VectorMatch
from app.services.rag.errors import KnowledgeBaseError
from app.services.rag.usage import estimate_stage_cost, model_snapshot
from app.services.token_quota_service import token_quota_service
from app.services.rag.judge import aggregate_rag_runs, apply_rag_feedback, calculate_rag_base, calculate_rag_final
from app.services.rule_evaluator import rule_evaluator

RAG_EXECUTION_TIMEOUT_SECONDS = 55 * 60
RAG_STALE_SECONDS = 60 * 60


def _replace_stage(detail: RagResponseDetail, usage: RagStageUsage, *, start: bool = False) -> None:
    stages = [RagStageUsage.model_validate(item) for item in detail.stage_usage_json]
    key = (usage.stage, usage.run_index)
    index = next((index for index, item in enumerate(stages) if (item.stage, item.run_index) == key), None)
    if start and index is not None:
        raise RagClientError("rag_stage_already_started", "此阶段已开始，不能重复调用")
    if index is None:
        if not start:
            raise RagClientError("rag_stage_not_started", "阶段尚未开始，不能保存用量")
        stages.append(usage)
    else:
        if stages[index].status != "pending" and stages[index] != usage:
            raise RagClientError("rag_usage_already_saved", "阶段用量已保存，不能覆盖")
        stages[index] = usage
    detail.stage_usage_json = [item.model_dump(mode="json", by_alias=True) for item in stages]


class RagEvaluationStore:
    """每次操作使用独立短事务，不允许候选并发共享 AsyncSession。"""
    def __init__(self, sessions: async_sessionmaker[AsyncSession] = AsyncSessionLocal) -> None:
        self.sessions = sessions

    async def create(self, user_id: int, knowledge_base_id: int, prompt: str,
                     models: list[RuntimeModelConfig], *, enable_thinking: bool,
                     conversation_id: int | None = None, visibility: Literal["public", "private"] = "private",
                     reserved_turn_id: int | None = None, reserved_response_id: int | None = None,
                     generation_epoch: int = 1) -> tuple[RagTaskContext, list[PreparedRagResponse]]:
        """初始化单轮任务或复用内部已预留的多轮任务，禁止重复创建回答。"""
        if not models or len({model.id for model in models}) != len(models):
            raise RagClientError("rag_invalid_models", "请选择不重复的候选模型")
        async with self.sessions() as db, db.begin():
            from app.services.multiturn.store import ConversationStore, require_legacy_conversation
            conversation = None
            if reserved_turn_id is not None:
                if conversation_id is None:
                    raise RagClientError("rag_reserved_turn_invalid", "预留轮次必须指定会话")
                conversation = await ConversationStore().get(db, conversation_id, user_id, owner_only=True, lock=True)
            else:
                await require_legacy_conversation(db, conversation_id, user_id)
            embedding_runtime = runtime_config(await get_config(db, lock=True))
            kb = await require_owned_knowledge_base(db, knowledge_base_id, user_id, lock=True)
            versions = await self._versions(db, user_id, kb.id)
            if kb.status != "ready" or not versions:
                raise KnowledgeBaseError("knowledge_base_not_ready", "知识库尚未就绪或没有可检索内容")
            if conversation is not None:
                knowledge = {"knowledgeBaseId": kb.id, "contentRevision": kb.content_revision,
                    "documents": [[document, revision] for document, revision in versions],
                    "embeddingCollection": embedding_runtime.rag_embedding_collection,
                    "embeddingRevision": embedding_runtime.rag_embedding_revision}
                task = await self._reserved_task(db, conversation, reserved_turn_id, prompt, models,
                                                 enable_thinking, knowledge, reserved_response_id=reserved_response_id,
                                                 generation_epoch=generation_epoch)
            else:
                task = EvaluationTask(user_id=user_id, prompt=prompt, task_type="rag", visibility=visibility,
                                      conversation_id=conversation_id, status="pending")
                db.add(task)
                await db.flush()
            context = RagTaskContext(task_id=task.id, user_id=user_id, knowledge_base_id=kb.id,
                conversation_turn_id=reserved_turn_id, generation_epoch=generation_epoch,
                embedding_runtime=embedding_runtime,
                content_revision=kb.content_revision, document_versions=versions, prompt=prompt,
                enable_thinking=enable_thinking)
            prepared: list[PreparedRagResponse] = []
            for model in models:
                if reserved_response_id is None:
                    response = ModelResponse(task_id=task.id, model_config_id=model.id, status="pending",
                        currency=model.currency, config_snapshot=model_snapshot(model).model_dump(mode="json", by_alias=True))
                    db.add(response)
                    await db.flush()
                else:
                    response = await db.get(ModelResponse, reserved_response_id)
                db.add(RagResponseDetail(response_id=response.id, knowledge_base_id=kb.id, knowledge_base_name=kb.name,
                    content_revision=kb.content_revision, embedding_revision=embedding_runtime.rag_embedding_collection if embedding_runtime.rag_embedding_protocol == "openai" else settings.rag_embedding_revision,
                    chunk_size=kb.chunk_size, chunk_overlap=kb.chunk_overlap,
                    document_versions_json=[{"documentId": document, "indexRevision": revision} for document, revision in versions]))
                prepared.append(PreparedRagResponse(response_id=response.id, model_config_id=model.id))
            return context.model_copy(update={"response_ids": tuple(item.response_id for item in prepared)}), prepared

    async def _reserved_task(self, db: AsyncSession, conversation: Conversation, turn_id: int,
                             prompt: str, models: list[RuntimeModelConfig], thinking: bool,
                             knowledge: dict[str, object], *, reserved_response_id: int | None = None,
                             generation_epoch: int = 1) -> EvaluationTask:
        """在会话锁内核对轮次、任务、模型和知识版本，返回唯一预留任务。"""
        from app.services.multiturn.catalog import model_identity
        config = conversation.config_json or {}
        if (conversation.mode != "rag" or conversation.generation_status != "generating"
                or (config.get("modelIds") != [model.id for model in models] if reserved_response_id is None
                    else len(models) != 1 or models[0].id not in config.get("modelIds", []))
                or config.get("enableThinking") != thinking):
            raise RagClientError("rag_reserved_turn_invalid", "预留会话模式、候选或生成配置不匹配")
        identities = config.get("identities", {})
        snapshots = config.get("models", [])
        if any(identities.get(str(model.id)) != model_identity(model)
               or model_snapshot(model).model_dump(mode="json", by_alias=True) not in snapshots for model in models):
            raise RagClientError("rag_reserved_config_changed", "预留会话模型快照与运行配置不匹配")
        if conversation.knowledge_snapshot_json != knowledge:
            raise RagClientError("knowledge_base_changed", "知识库或 Embedding 版本已变化，请创建新会话")
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id,
            ConversationTurn.conversation_id == conversation.id).with_for_update())
        if (turn is None or turn.turn_index != conversation.current_turn
                or turn.generation_status != "generating" or turn.prompt != prompt or turn.generation_epoch != generation_epoch):
            raise RagClientError("rag_reserved_turn_invalid", "预留轮次已变化或问题不匹配")
        task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == turn.task_id,
            EvaluationTask.conversation_id == conversation.id, EvaluationTask.user_id == conversation.user_id,
            EvaluationTask.task_type == "rag").with_for_update())
        if task is None or task.status != "pending" or task.prompt != prompt:
            raise RagClientError("rag_reserved_task_invalid", "预留任务不存在或已结束")
        if reserved_response_id is not None:
            response = await db.get(ModelResponse, reserved_response_id)
            if (response is None or response.task_id != task.id or response.model_config_id != models[0].id
                    or response.status != "pending" or (response.config_snapshot or {}).get("branchAction") != "retry"
                    or await db.scalar(select(RagResponseDetail.response_id).where(RagResponseDetail.response_id == reserved_response_id)) is not None):
                raise RagClientError("rag_reserved_task_started", "重试回答不匹配或已初始化")
        elif await db.scalar(select(ModelResponse.id).where(ModelResponse.task_id == task.id).limit(1)) is not None:
            raise RagClientError("rag_reserved_task_started", "预留任务已初始化，不能重复创建回答")
        return task

    async def _current_generation(self, db: AsyncSession, context: RagTaskContext) -> bool:
        """旧生成代次不能读取或结束同一轮的新重试，单轮旧任务保持兼容。"""
        if context.conversation_turn_id is None:
            return True
        return await db.scalar(select(ConversationTurn.id).where(ConversationTurn.id == context.conversation_turn_id,
            ConversationTurn.task_id == context.task_id, ConversationTurn.generation_epoch == context.generation_epoch)) is not None

    async def _versions(self, db: AsyncSession, user_id: int, knowledge_base_id: int) -> list[tuple[int, int]]:
        rows = await db.execute(select(KnowledgeDocument.id, KnowledgeDocument.index_revision).where(
            KnowledgeDocument.user_id == user_id, KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.status == "ready", KnowledgeDocument.chunk_count > 0,
        ).order_by(KnowledgeDocument.id).with_for_update())
        return [(row[0], row[1]) for row in rows.all()]

    async def _owned_response(self, db: AsyncSession, context: RagTaskContext,
                              response_id: int, *, allow_expired: bool = False) -> tuple[ModelResponse, RagResponseDetail]:
        task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == context.task_id,
            EvaluationTask.user_id == context.user_id, EvaluationTask.task_type == "rag")
            .with_for_update().execution_options(populate_existing=True))
        if task is None:
            raise RagClientError("rag_response_not_found", "评测回答不存在或无权访问")
        if (not await self._current_generation(db, context)
                or (context.response_ids and response_id not in context.response_ids)):
            raise RagClientError("rag_attempt_ended", "当前生成尝试已经结束")
        if not allow_expired:
            expired = task.created_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=RAG_STALE_SECONDS)
            if expired and task.status == "pending":
                connection = await db.connection()
                if await connection.run_sync(lambda sync: inspect(sync).has_table("conversation_turns")):
                    from app.services.multiturn.generation_recovery import GENERATION_STALE_SECONDS
                    live = await db.scalar(select(ConversationTurn.id).join(Conversation,
                        Conversation.id == ConversationTurn.conversation_id).where(ConversationTurn.task_id == task.id,
                        ConversationTurn.generation_status == "generating", Conversation.generation_status == "generating",
                        Conversation.current_turn == ConversationTurn.turn_index,
                        Conversation.updated_at >= datetime.utcnow() - timedelta(seconds=GENERATION_STALE_SECONDS)))
                    expired = live is None
            if task.status != "pending" or expired:
                raise RagClientError("rag_task_finished", "任务已结束或超过有效期，不能继续写入")
        response = await db.scalar(select(ModelResponse).where(ModelResponse.id == response_id,
            ModelResponse.task_id == context.task_id).with_for_update().execution_options(populate_existing=True))
        detail = await db.scalar(select(RagResponseDetail).where(RagResponseDetail.response_id == response_id,
            RagResponseDetail.knowledge_base_id == context.knowledge_base_id,
            RagResponseDetail.content_revision == context.content_revision).with_for_update().execution_options(populate_existing=True))
        if response is None or detail is None:
            raise RagClientError("rag_response_not_found", "评测回答不存在或无权访问")
        return response, detail

    async def save_stage(self, context: RagTaskContext, response_id: int, usage: RagStageUsage,
                          *, start: bool = False) -> None:
        async with self.sessions() as db, db.begin():
            response, detail = await self._owned_response(db, context, response_id)
            if response.status in ("success", "failed"):
                raise RagClientError("rag_response_finished", "已结束的回答不能再次调用模型")
            if usage.stage in ("generate", "judge") and (not detail.evidence_json or detail.failure_stage is not None):
                raise RagClientError("rag_snapshot_missing", "缺少已固定的检索证据")
            _replace_stage(detail, usage, start=start)
            if response.status == "pending":
                response.status = "running"

    async def read_evidence(self, context: RagTaskContext, matches: list[VectorMatch]) -> list[RagEvidence]:
        if not matches or len(matches) > 5 or len({match.chunk_id for match in matches}) != len(matches):
            raise RagClientError("rag_invalid_evidence", "检索证据数量或标识无效")
        async with self.sessions() as db:
            rows = (await db.execute(select(KnowledgeChunk, KnowledgeDocument.original_name).join(
                KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).where(
                KnowledgeChunk.id.in_([match.chunk_id for match in matches]),
                KnowledgeChunk.user_id == context.user_id, KnowledgeChunk.knowledge_base_id == context.knowledge_base_id,
                KnowledgeDocument.user_id == context.user_id, KnowledgeDocument.knowledge_base_id == context.knowledge_base_id,
                KnowledgeDocument.status == "ready", KnowledgeDocument.index_revision == KnowledgeChunk.index_revision,
                tuple_(KnowledgeChunk.document_id, KnowledgeChunk.index_revision).in_(context.document_versions),
            ))).all()
            by_id = {chunk.id: (chunk, name) for chunk, name in rows}
            evidence: list[RagEvidence] = []
            for index, match in enumerate(matches):
                row = by_id.get(match.chunk_id)
                if row is None or (row[0].document_id, row[0].index_revision, row[0].chunk_index) != (
                    match.document_id, match.index_revision, match.chunk_index):
                    raise RagClientError("rag_evidence_changed", "检索证据已变化或无法读取")
                chunk, name = row
                evidence.append(RagEvidence(label=f"S{index + 1}", document_id=chunk.document_id, document_name=name,
                    chunk_id=chunk.id, index_revision=chunk.index_revision, text=chunk.text,
                    similarity=match.similarity, source=chunk.source_json))
            return evidence

    async def fix_snapshots(self, context: RagTaskContext, prepared: list[PreparedRagResponse]) -> bool:
        """固定所有候选证据前重新校验索引与 Embedding 语义版本。"""
        async with self.sessions() as db, db.begin():
            current_embedding = runtime_config(await get_config(db, lock=True))
            kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == context.knowledge_base_id,
                KnowledgeBase.user_id == context.user_id).with_for_update())
            valid = kb is not None and kb.status == "ready" and kb.content_revision == context.content_revision
            original_embedding = context.embedding_runtime or settings
            valid = valid and (current_embedding.rag_embedding_collection, current_embedding.rag_embedding_revision) == (
                original_embedding.rag_embedding_collection, original_embedding.rag_embedding_revision)
            if valid:
                valid = await self._versions(db, context.user_id, context.knowledge_base_id) == context.document_versions
            response_ids = list((await db.scalars(select(ModelResponse.id).where(
                ModelResponse.task_id == context.task_id).order_by(ModelResponse.id))).all())
            if sorted(item.response_id for item in prepared) != response_ids:
                raise RagClientError("rag_incomplete_snapshot", "必须同时固定本任务全部候选的检索结果")
            for item in sorted(prepared, key=lambda value: value.response_id):
                response, detail = await self._owned_response(db, context, item.response_id)
                if response.status not in ("pending", "running") or detail.evidence_json:
                    raise RagClientError("rag_snapshot_already_saved", "证据已固定，不能被后续检索覆盖")
                if not valid and item.failure_stage is None:
                    item.failure_stage, item.error_code, item.evidence = "snapshot", "knowledge_base_changed", []
                detail.rewritten_query = item.rewritten_query
                detail.evidence_json = [value.model_dump(mode="json", by_alias=True) for value in item.evidence]
                detail.failure_stage, detail.error_code = item.failure_stage, item.error_code
            return valid

    async def save_answer(self, context: RagTaskContext, prepared: PreparedRagResponse,
                           answer: str, usage: RagStageUsage | None) -> None:
        async with self.sessions() as db, db.begin():
            response, detail = await self._owned_response(db, context, prepared.response_id)
            if response.status in ("success", "failed", "answer_completed"):
                raise RagClientError("rag_response_finished", "回答已保存，不能重复生成")
            if prepared.failure_stage is None and (not detail.evidence_json or detail.evidence_json != [
                item.model_dump(mode="json", by_alias=True) for item in prepared.evidence
            ]):
                raise RagClientError("rag_snapshot_missing", "生成所用证据与固定快照不一致")
            response.answer_text = answer
            response.status = "failed" if prepared.failure_stage else "answer_completed"
            detail.failure_stage, detail.error_code = prepared.failure_stage, prepared.error_code
            response.error_message = prepared.error_code
            if usage is not None:
                response.latency_ms = usage.latency_ms or 0
                # 顶层沿用回答生成阶段；未知总量仍在明细中显式标记，不能视为精确零。
                response.input_tokens = usage.input_tokens or 0
                response.output_tokens = usage.output_tokens or 0
                response.cache_hit_tokens = usage.cache_hit_tokens or 0
                response.cache_creation_tokens = usage.cache_creation_tokens or 0
                response.total_tokens = usage.total_tokens or 0
                response.estimated_cost = usage.estimated_cost or Decimal("0")
                if usage.model is not None and usage.status == "known":
                    costs = estimate_stage_cost(usage.model, ModelUsage(response.input_tokens, response.output_tokens,
                        response.cache_hit_tokens, response.cache_creation_tokens))
                    response.input_cost, response.output_cost = costs.input_cost, costs.output_cost
                    response.cache_hit_cost, response.cache_creation_cost = costs.cache_hit_cost, costs.cache_creation_cost

    async def interrupt(self, context: RagTaskContext, *, stale_before: datetime | None = None) -> None:
        """在调用方确认本任务已停止后收尾；仅记账，不重放任何模型请求。"""
        async with self.sessions() as db, db.begin():
            task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == context.task_id,
                EvaluationTask.user_id == context.user_id, EvaluationTask.task_type == "rag").with_for_update())
            if task is None:
                raise RagClientError("rag_task_not_found", "评测任务不存在或无权访问")
            if not await self._current_generation(db, context):
                return
            if task.status in ("completed", "failed", "interrupted"):
                return
            if stale_before is not None and task.created_at >= stale_before:
                return
            response_query = select(ModelResponse.id).where(ModelResponse.task_id == context.task_id)
            if context.response_ids:
                response_query = response_query.where(ModelResponse.id.in_(context.response_ids))
            response_ids = list((await db.scalars(response_query.order_by(ModelResponse.id))).all())
            has_success = False
            for response_id in response_ids:
                response, detail = await self._owned_response(db, context, response_id, allow_expired=True)
                if response.status == "success":
                    has_success = True
                    continue
                stages = [RagStageUsage.model_validate(value) for value in detail.stage_usage_json]
                for usage in stages:
                    if usage.status == "pending":
                        usage.status = "unknown"
                        if detail.failure_stage is None:
                            detail.failure_stage = usage.stage
                detail.stage_usage_json = [value.model_dump(mode="json", by_alias=True) for value in stages]
                detail.failure_stage = detail.failure_stage or ("judge" if response.answer_text else "snapshot")
                detail.error_code = detail.error_code or "rag_interrupted"
                response.status, response.error_message = "failed", detail.error_code
                result = await db.scalar(select(EvaluationResult).where(EvaluationResult.response_id == response_id))
                if result is None:
                    db.add(EvaluationResult(response_id=response_id, final_score=None, excluded_from_stats=True,
                        score_status="judge_failed" if detail.failure_stage == "judge" else "model_failed"))
                await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=context.user_id)
            task.status = "completed" if has_success else "failed"
            task.completed_at = datetime.now(UTC).replace(tzinfo=None)

    async def recover_interrupted(self, *, limit: int = 100) -> None:
        """仅按旧时限收尾单轮任务，多轮存活与异常收尾由独立机制负责。"""
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=RAG_STALE_SECONDS)
        async with self.sessions() as db:
            connection = await db.connection()
            migrated = await connection.run_sync(lambda sync: inspect(sync).has_table("conversation_turns"))
            statement = select(EvaluationTask).where(
                EvaluationTask.task_type == "rag",
                EvaluationTask.status == "pending", EvaluationTask.created_at < cutoff,
            )
            if migrated:
                statement = statement.where(~EvaluationTask.id.in_(select(ConversationTurn.task_id)))
            tasks = list((await db.scalars(statement.order_by(EvaluationTask.id).limit(limit))).all())
            contexts: list[RagTaskContext] = []
            for task in tasks:
                detail = await db.scalar(select(RagResponseDetail).join(ModelResponse,
                    ModelResponse.id == RagResponseDetail.response_id).where(ModelResponse.task_id == task.id)
                    .order_by(ModelResponse.id).limit(1))
                if detail is not None:
                    contexts.append(RagTaskContext(task_id=task.id, user_id=task.user_id,
                        knowledge_base_id=detail.knowledge_base_id, content_revision=detail.content_revision,
                        document_versions=[(item["documentId"], item["indexRevision"]) for item in detail.document_versions_json],
                        prompt=task.prompt, enable_thinking=False))
        for context in contexts:
            await self.interrupt(context, stale_before=cutoff)

    async def save_judge_run(self, context: RagTaskContext, response_id: int,
                             run: RagJudgeRun, usage: RagStageUsage) -> None:
        async with self.sessions() as db, db.begin():
            response, detail = await self._owned_response(db, context, response_id)
            if response.status != "answer_completed" or run.run_index != usage.run_index or usage.stage != "judge":
                raise RagClientError("rag_invalid_judge_state", "回答或评审轮次状态无效")
            if any(value["runIndex"] == run.run_index for value in detail.judge_runs_json):
                raise RagClientError("rag_judge_already_saved", "此轮评审已保存，不能覆盖")
            _replace_stage(detail, usage)
            detail.judge_runs_json = [*detail.judge_runs_json, run.model_dump(mode="json", by_alias=True)]

    async def saved_answer(self, context: RagTaskContext, response_id: int) -> str:
        async with self.sessions() as db, db.begin():
            response, _ = await self._owned_response(db, context, response_id)
            return response.answer_text

    async def finalize_response(self, context: RagTaskContext, response_id: int) -> None:
        async with self.sessions() as db, db.begin():
            response, detail = await self._owned_response(db, context, response_id)
            if await db.scalar(select(EvaluationResult.id).where(EvaluationResult.response_id == response_id)) is not None:
                return
            rule = EvaluationScoreRead(**rule_evaluator.evaluate(prompt=context.prompt, answer=response.answer_text))
            status = "model_failed"
            quality = None
            ranges = None
            if detail.failure_stage is None:
                aggregate = aggregate_rag_runs([RagJudgeRun.model_validate(value) for value in detail.judge_runs_json])
                status = aggregate.score_status
                detail.faithfulness = aggregate.faithfulness
                detail.citation_correctness = aggregate.citation_correctness
                detail.citation_completeness = aggregate.citation_completeness
                ranges = max(aggregate.ranges.values()) if aggregate.ranges else None
                if status == "scored":
                    quality = aggregate.answer_quality
                    assert quality is not None and aggregate.faithfulness is not None
                    assert aggregate.citation_correctness is not None and aggregate.citation_completeness is not None
                    detail.rag_final = calculate_rag_final(aggregate.faithfulness, aggregate.citation_correctness, aggregate.citation_completeness)
                    detail.base_final = calculate_rag_base(Decimal(str(rule.rule_final)), quality,
                        aggregate.faithfulness, aggregate.citation_correctness, aggregate.citation_completeness).quantize(Decimal("0.0000000001"))
                else:
                    detail.failure_stage, detail.error_code = "judge", f"rag_{status}"
            response.status = "failed" if status == "model_failed" else "success"
            # 允许历史页在评审期间提交反馈；终态必须使用当前反馈，不能重置为零票。
            feedback = list((await db.scalars(select(UserFeedback.feedback_type).where(
                UserFeedback.response_id == response_id))).all())
            db.add(EvaluationResult(response_id=response_id,
                relevance_score=Decimal(str(rule.relevance)), completeness_score=Decimal(str(rule.completeness)),
                clarity_score=Decimal(str(rule.clarity)), format_score=Decimal(str(rule.format)), safety_score=Decimal(str(rule.safety)),
                rule_score=Decimal(str(rule.rule_final)), judge_score=quality,
                final_score=apply_rag_feedback(detail.base_final, feedback.count("like"), feedback.count("dislike"))
                    if status == "scored" and detail.base_final is not None else None,
                score_status=status, excluded_from_stats=status != "scored", judge_score_range=ranges,
                rule_dictionary_version=rule.rule_dictionary_version, judge_prompt_version="rag-v1"))
            await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=context.user_id)

    async def finalize_multiturn_response(self, context: RagTaskContext, response_id: int) -> None:
        """多轮回答独立结算生成，评分作业另存且不覆盖可用于续聊的回答。"""
        from app.services.multiturn.store import ConversationStore
        async with self.sessions() as db, db.begin():
            task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == context.task_id,
                EvaluationTask.user_id == context.user_id, EvaluationTask.task_type == "rag"))
            if task is None or task.conversation_id is None:
                raise RagClientError("rag_reserved_task_invalid", "任务不属于多轮 RAG 会话")
            conversation = await ConversationStore().get(db, task.conversation_id, context.user_id,
                                                          owner_only=True, lock=True)
            if conversation.mode != "rag":
                raise RagClientError("rag_reserved_task_invalid", "任务不属于多轮 RAG 会话")
            response, detail = await self._owned_response(db, context, response_id)
            existing = await db.scalar(select(EvaluationResult).where(EvaluationResult.response_id == response_id))
            if existing is not None:
                if existing.judge_prompt_version != "rag-multiturn-v1":
                    raise RagClientError("rag_score_conflict", "回答已由其他评分流程结算")
                return
            if response.status not in ("answer_completed", "failed"):
                raise RagClientError("rag_answer_pending", "回答生成尚未结束，不能结算")
            success = response.status == "answer_completed" and detail.failure_stage is None
            if success and (not response.answer_text.strip() or not detail.evidence_json):
                raise RagClientError("rag_snapshot_missing", "成功回答必须包含正文和固定证据")
            response.status = "success" if success else "failed"
            db.add(EvaluationResult(response_id=response_id, final_score=None, excluded_from_stats=True,
                score_status="judge_disabled" if success else "model_failed", judge_prompt_version="rag-multiturn-v1"))
            await token_quota_service.record_rag_usage(db, response_id=response_id, user_id=context.user_id)

    async def finish_task(self, context: RagTaskContext) -> None:
        async with self.sessions() as db, db.begin():
            task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == context.task_id,
                EvaluationTask.user_id == context.user_id, EvaluationTask.task_type == "rag").with_for_update())
            if task is None or task.status != "pending":
                return
            if not await self._current_generation(db, context):
                return
            statuses = list((await db.scalars(select(ModelResponse.status).where(ModelResponse.task_id == context.task_id))).all())
            if any(value not in ("success", "failed") for value in statuses):
                raise RagClientError("rag_task_incomplete", "仍有回答未完成持久化")
            task.status = "completed" if "success" in statuses else "failed"
            task.completed_at = datetime.now(UTC).replace(tzinfo=None)
