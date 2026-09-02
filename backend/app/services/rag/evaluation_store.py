from decimal import Decimal
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.adapters.base import ModelUsage
from app.models.evaluation import EvaluationResult, EvaluationTask
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.rag import RagResponseDetail
from app.models.response import ModelResponse
from app.schemas.rag import PreparedRagResponse, RagEvidence, RagStageUsage, RagTaskContext
from app.services.knowledge_base_service import require_owned_knowledge_base
from app.services.model_config_service import RuntimeModelConfig
from app.services.rag.clients import RagClientError, VectorMatch
from app.services.rag.errors import KnowledgeBaseError
from app.services.rag.usage import estimate_stage_cost, model_snapshot
from app.services.token_quota_service import token_quota_service


class RagEvaluationStore:
    """每次操作使用独立短事务，不允许候选并发共享 AsyncSession。"""
    def __init__(self, sessions: async_sessionmaker[AsyncSession] = AsyncSessionLocal) -> None:
        self.sessions = sessions

    async def create(self, user_id: int, knowledge_base_id: int, prompt: str,
                     models: list[RuntimeModelConfig], *, enable_thinking: bool,
                     conversation_id: int | None = None) -> tuple[RagTaskContext, list[PreparedRagResponse]]:
        if not models or len({model.id for model in models}) != len(models):
            raise RagClientError("rag_invalid_models", "请选择不重复的候选模型")
        async with self.sessions() as db, db.begin():
            kb = await require_owned_knowledge_base(db, knowledge_base_id, user_id, lock=True)
            versions = await self._versions(db, user_id, kb.id)
            if kb.status != "ready" or not versions:
                raise KnowledgeBaseError("knowledge_base_not_ready", "知识库尚未就绪或没有可检索内容")
            task = EvaluationTask(user_id=user_id, prompt=prompt, task_type="rag", visibility="private",
                                  conversation_id=conversation_id, status="pending")
            db.add(task)
            await db.flush()
            context = RagTaskContext(task_id=task.id, user_id=user_id, knowledge_base_id=kb.id,
                content_revision=kb.content_revision, document_versions=versions, prompt=prompt,
                enable_thinking=enable_thinking)
            prepared: list[PreparedRagResponse] = []
            for model in models:
                response = ModelResponse(task_id=task.id, model_config_id=model.id, status="pending",
                    currency=model.currency, config_snapshot=model_snapshot(model).model_dump(mode="json", by_alias=True))
                db.add(response)
                await db.flush()
                db.add(RagResponseDetail(response_id=response.id, knowledge_base_id=kb.id, knowledge_base_name=kb.name,
                    content_revision=kb.content_revision, embedding_revision=settings.rag_embedding_revision,
                    chunk_size=kb.chunk_size, chunk_overlap=kb.chunk_overlap,
                    document_versions_json=[{"documentId": document, "indexRevision": revision} for document, revision in versions]))
                prepared.append(PreparedRagResponse(response_id=response.id, model_config_id=model.id))
            return context, prepared

    async def _versions(self, db: AsyncSession, user_id: int, knowledge_base_id: int) -> list[tuple[int, int]]:
        rows = await db.execute(select(KnowledgeDocument.id, KnowledgeDocument.index_revision).where(
            KnowledgeDocument.user_id == user_id, KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.status == "ready", KnowledgeDocument.chunk_count > 0,
        ).order_by(KnowledgeDocument.id).with_for_update())
        return [(row[0], row[1]) for row in rows.all()]

    async def _owned_response(self, db: AsyncSession, context: RagTaskContext,
                              response_id: int) -> tuple[ModelResponse, RagResponseDetail]:
        task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == context.task_id,
            EvaluationTask.user_id == context.user_id, EvaluationTask.task_type == "rag", EvaluationTask.visibility == "private"))
        if task is None:
            raise RagClientError("rag_response_not_found", "评测回答不存在或无权访问")
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
            stages = [RagStageUsage.model_validate(item) for item in detail.stage_usage_json]
            key = (usage.stage, usage.run_index)
            index = next((index for index, item in enumerate(stages) if (item.stage, item.run_index) == key), None)
            if start and index is not None:
                raise RagClientError("rag_stage_already_started", "此阶段已开始，不能重复调用")
            if usage.stage in ("generate", "judge") and (not detail.evidence_json or detail.failure_stage is not None):
                raise RagClientError("rag_snapshot_missing", "缺少已固定的检索证据")
            if index is None:
                if not start:
                    raise RagClientError("rag_stage_not_started", "阶段尚未开始，不能保存用量")
                stages.append(usage)
            else:
                if stages[index].status != "pending" and stages[index] != usage:
                    raise RagClientError("rag_usage_already_saved", "阶段用量已保存，不能覆盖")
                stages[index] = usage
            detail.stage_usage_json = [item.model_dump(mode="json", by_alias=True) for item in stages]
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
        async with self.sessions() as db, db.begin():
            kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == context.knowledge_base_id,
                KnowledgeBase.user_id == context.user_id).with_for_update())
            valid = kb is not None and kb.status == "ready" and kb.content_revision == context.content_revision
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

    async def interrupt(self, context: RagTaskContext) -> None:
        """在调用方确认本任务已停止后收尾；仅记账，不重放任何模型请求。"""
        async with self.sessions() as db, db.begin():
            task = await db.scalar(select(EvaluationTask).where(EvaluationTask.id == context.task_id,
                EvaluationTask.user_id == context.user_id, EvaluationTask.task_type == "rag",
                EvaluationTask.visibility == "private").with_for_update())
            if task is None:
                raise RagClientError("rag_task_not_found", "评测任务不存在或无权访问")
            if task.status in ("completed", "failed"):
                return
            response_ids = list((await db.scalars(select(ModelResponse.id).where(
                ModelResponse.task_id == context.task_id).order_by(ModelResponse.id))).all())
            has_success = False
            for response_id in response_ids:
                response, detail = await self._owned_response(db, context, response_id)
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
