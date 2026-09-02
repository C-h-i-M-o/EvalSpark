from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, RagJob
from app.schemas.knowledge_base import (
    JobAccepted, JobOperation, KnowledgeBaseCreate, KnowledgeBaseList, KnowledgeBasePatch,
    KnowledgeBaseRead, KnowledgeDocumentList, KnowledgeDocumentRead, RagJobRead,
)
from app.services.rag.documents import resolve_storage_path, store_upload
from app.services.rag.errors import KnowledgeBaseError

logger = logging.getLogger(__name__)
ACTIVE_JOB_STATES = ("queued", "running")


async def require_owned_knowledge_base(
    db: AsyncSession, knowledge_base_id: int, user_id: int, *, lock: bool = False, include_deleted: bool = False,
) -> KnowledgeBase:
    statement = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id, KnowledgeBase.user_id == user_id)
    if not include_deleted:
        statement = statement.where(KnowledgeBase.status != "deleted")
    if lock:
        # 重新读取最新版本，不能沿用上传前身份映射中的旧状态。
        statement = statement.with_for_update().execution_options(populate_existing=True)
    kb = await db.scalar(statement)
    if kb is None:
        raise KnowledgeBaseError("knowledge_base_not_found", "知识库不存在或无权访问", 404)
    return kb


def publish_rag_job(job_id: str) -> bool:
    from app.worker import celery_app
    name = "app.worker.run_rag_job"
    if name not in celery_app.tasks:
        # 索引阶段注册任务前只保留数据库队列，避免 Worker 丢弃未知任务。
        return False
    celery_app.send_task(name, args=[job_id], queue="rag", retry=False)
    return True


class KnowledgeBaseService:
    def __init__(self, *, documents_dir: Path = settings.rag_documents_dir, publisher: Callable[[str], bool] = publish_rag_job) -> None:
        self.documents_dir = documents_dir
        self.publisher = publisher

    async def _counts(self, db: AsyncSession, kb_ids: list[int]) -> dict[int, tuple[int, int]]:
        if not kb_ids:
            return {}
        rows = (await db.execute(select(
            KnowledgeDocument.knowledge_base_id, func.count(KnowledgeDocument.id),
            func.sum(case((KnowledgeDocument.status == "ready", KnowledgeDocument.chunk_count), else_=0)),
        ).where(
            KnowledgeDocument.knowledge_base_id.in_(kb_ids), KnowledgeDocument.status != "deleted",
        ).group_by(KnowledgeDocument.knowledge_base_id))).all()
        return {int(row[0]): (int(row[1]), int(row[2] or 0)) for row in rows}

    def _read(self, kb: KnowledgeBase, counts: tuple[int, int]) -> KnowledgeBaseRead:
        return KnowledgeBaseRead(
            id=kb.id, name=kb.name, description=kb.description, chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap, status=kb.status, content_revision=kb.content_revision,
            document_count=counts[0], chunk_count=counts[1], available=kb.status == "ready" and counts[1] > 0,
            error_code=kb.error_code, created_at=kb.created_at, updated_at=kb.updated_at,
        )

    async def create(self, db: AsyncSession, user_id: int, payload: KnowledgeBaseCreate) -> KnowledgeBaseRead:
        kb = KnowledgeBase(user_id=user_id, **payload.model_dump())
        db.add(kb)
        await db.commit()
        return self._read(kb, (0, 0))

    async def get(self, db: AsyncSession, user_id: int, kb_id: int) -> KnowledgeBaseRead:
        kb = await require_owned_knowledge_base(db, kb_id, user_id)
        counts = await self._counts(db, [kb_id])
        return self._read(kb, counts.get(kb_id, (0, 0)))

    async def list(self, db: AsyncSession, user_id: int, page: int, page_size: int) -> KnowledgeBaseList:
        condition = (KnowledgeBase.user_id == user_id, KnowledgeBase.status != "deleted")
        total = await db.scalar(select(func.count()).select_from(KnowledgeBase).where(*condition))
        rows = (await db.scalars(select(KnowledgeBase).where(*condition).order_by(
            KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size))).all()
        counts = await self._counts(db, [kb.id for kb in rows])
        return KnowledgeBaseList(items=[self._read(kb, counts.get(kb.id, (0, 0))) for kb in rows], total=total or 0, page=page, page_size=page_size)

    def _ensure_writable(self, kb: KnowledgeBase) -> None:
        if kb.status in ("deleting", "deleted"):
            raise KnowledgeBaseError("knowledge_base_deleting", "知识库正在删除，不能修改")

    async def _active_job(self, db: AsyncSession, kb_id: int, *, document_id: int | None = None, operation: str | None = None) -> RagJob | None:
        query = select(RagJob).where(RagJob.knowledge_base_id == kb_id, RagJob.status.in_(ACTIVE_JOB_STATES))
        if document_id is not None:
            query = query.where(RagJob.document_id == document_id)
        if operation is not None:
            query = query.where(RagJob.operation == operation)
        return await db.scalar(query.order_by(RagJob.created_at, RagJob.id).limit(1))

    async def patch(self, db: AsyncSession, user_id: int, kb_id: int, payload: KnowledgeBasePatch) -> KnowledgeBaseRead:
        await db.commit()  # 结束鉴权读取快照，锁后子查询必须看到最新已提交状态。
        try:
            kb = await require_owned_knowledge_base(db, kb_id, user_id, lock=True)
            self._ensure_writable(kb)
            changes = payload.model_dump(exclude_unset=True)
            try:
                merged = KnowledgeBaseCreate(**{
                    **{name: getattr(kb, name) for name in ("name", "description", "chunk_size", "chunk_overlap")}, **changes,
                })
            except ValidationError:
                raise KnowledgeBaseError("invalid_chunking", "重叠 Token 数必须小于切块大小", 422) from None
            chunking_changed = (merged.chunk_size, merged.chunk_overlap) != (kb.chunk_size, kb.chunk_overlap)
            if chunking_changed and await self._active_job(db, kb_id):
                raise KnowledgeBaseError("knowledge_base_busy", "知识库仍有处理任务，请完成后修改切分参数")
            for name in changes:
                setattr(kb, name, getattr(merged, name))
            if chunking_changed:
                kb.content_revision += 1
                counts = await self._counts(db, [kb_id])
                kb.status = "reindex_required" if counts.get(kb_id, (0, 0))[0] else "empty"
                kb.error_code = None
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return await self.get(db, user_id, kb_id)

    async def _document(self, db: AsyncSession, user_id: int, kb_id: int, document_id: int, *, include_deleted: bool = False) -> KnowledgeDocument:
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id, KnowledgeDocument.knowledge_base_id == kb_id, KnowledgeDocument.user_id == user_id,
        ).execution_options(populate_existing=True)
        if not include_deleted:
            query = query.where(KnowledgeDocument.status != "deleted")
        document = await db.scalar(query)
        if document is None:
            raise KnowledgeBaseError("document_not_found", "文档不存在或无权访问", 404)
        return document

    async def _job(self, db: AsyncSession, kb: KnowledgeBase, operation: JobOperation, document: KnowledgeDocument | None = None) -> RagJob:
        revision = document.index_revision if document else kb.content_revision
        job_id = str(uuid5(NAMESPACE_URL, f"evalspark:rag:{kb.id}:{document.id if document else 0}:{operation}:{revision}"))
        job = await db.get(RagJob, job_id)
        if job is None:
            job = RagJob(id=job_id, knowledge_base_id=kb.id, document_id=document.id if document else None, operation=operation, target_revision=revision)
            db.add(job)
        elif job.status == "failed":
            # 人工重试生成新版本，保留旧领取代次和在途请求冷却窗口。
            kb.content_revision += 1
            if document is not None:
                document.index_revision += 1
            return await self._job(db, kb, operation, document)
        await db.flush()
        return job

    async def _notify(self, db: AsyncSession, job: RagJob) -> JobAccepted:
        response = JobAccepted(document_id=job.document_id, job_id=job.id, status=job.status, dispatch_pending=job.status == "queued" and job.dispatched_at is None)
        if not response.dispatch_pending:
            return response
        try:
            if await run_in_threadpool(self.publisher, job.id):
                await db.execute(update(RagJob).where(RagJob.id == response.job_id).values(dispatched_at=datetime.now(UTC).replace(tzinfo=None)))
                await db.commit()
                response.dispatch_pending = False
        except Exception:
            # 已提交的文件和作业必须保留；恢复循环负责补投，不回显 broker 原始异常。
            await db.rollback()
            logger.warning("RAG 作业通知暂未完成，等待恢复补投")
        return response

    async def upload(self, db: AsyncSession, user_id: int, kb_id: int, upload: UploadFile) -> JobAccepted:
        kb = await require_owned_knowledge_base(db, kb_id, user_id)
        self._ensure_writable(kb)
        await db.commit()  # 释放上传前读取事务，不持锁等待文件处理。
        stored = await store_upload(upload, root=self.documents_dir)
        try:
            kb = await require_owned_knowledge_base(db, kb_id, user_id, lock=True)
            self._ensure_writable(kb)
            if kb.status in ("reindex_required", "failed") or await self._active_job(db, kb_id, operation="reindex"):
                raise KnowledgeBaseError("reindex_required", "请先完成知识库重建，再上传文档")
            count = await db.scalar(select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == kb_id, KnowledgeDocument.status != "deleted",
            ))
            if (count or 0) >= 100:
                raise KnowledgeBaseError("document_limit", "每个知识库最多保留 100 份未清理文档")
            document = KnowledgeDocument(
                knowledge_base_id=kb_id, user_id=user_id, storage_key=stored.storage_key, original_name=stored.original_name,
                media_type=stored.media_type, size_bytes=stored.size_bytes, content_hash=stored.content_hash,
            )
            db.add(document)
            kb.content_revision += 1
            kb.status, kb.error_code = "indexing", None
            await db.flush()
            job = await self._job(db, kb, "index", document)
            await db.commit()
        except BaseException:
            try:
                await db.rollback()
                # 连接中断可能发生在提交成功之后；重新核对引用，不能误删已入库的原文。
                referenced = await db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.storage_key == stored.storage_key))
                if referenced is None:
                    await run_in_threadpool(resolve_storage_path(stored.storage_key, root=self.documents_dir).unlink, missing_ok=True)
            except Exception:
                logger.warning("RAG 上传未能确认文件引用或完成清理，保留文件待检查")
            raise
        return await self._notify(db, job)

    async def list_documents(self, db: AsyncSession, user_id: int, kb_id: int, page: int, page_size: int) -> KnowledgeDocumentList:
        await require_owned_knowledge_base(db, kb_id, user_id)
        condition = (KnowledgeDocument.knowledge_base_id == kb_id, KnowledgeDocument.user_id == user_id, KnowledgeDocument.status != "deleted")
        total = await db.scalar(select(func.count()).select_from(KnowledgeDocument).where(*condition))
        documents = (await db.scalars(select(KnowledgeDocument).where(*condition).order_by(
            KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size))).all()
        items = []
        for document in documents:
            job = await db.scalar(select(RagJob).where(
                RagJob.document_id == document.id, RagJob.target_revision == document.index_revision,
            ).order_by(RagJob.created_at.desc(), RagJob.id.desc()).limit(1))
            if job is None:
                # 整库作业没有 document_id，不能把旧版本单文档任务当成重建进度。
                job = await db.scalar(select(RagJob).where(
                    RagJob.knowledge_base_id == kb_id, RagJob.document_id.is_(None),
                    RagJob.status.in_(ACTIVE_JOB_STATES),
                ).order_by(RagJob.target_revision.desc()).limit(1))
            data = KnowledgeDocumentRead.model_validate(document)
            if job:
                data.current_job = RagJobRead.model_validate(job)
                data.current_job.dispatch_pending = job.status == "queued" and job.dispatched_at is None
            items.append(data)
        return KnowledgeDocumentList(items=items, total=total or 0, page=page, page_size=page_size)

    async def download(self, db: AsyncSession, user_id: int, kb_id: int, document_id: int) -> tuple[Path, str, str]:
        kb = await require_owned_knowledge_base(db, kb_id, user_id)
        document = await self._document(db, user_id, kb_id, document_id)
        if kb.status == "deleting" or document.status == "deleting":
            raise KnowledgeBaseError("document_not_found", "文档不存在或已开始删除", 404)
        path = resolve_storage_path(document.storage_key, root=self.documents_dir)
        if not path.is_file():
            raise KnowledgeBaseError("document_not_found", "原文件不存在", 404)
        return path, document.original_name, document.media_type

    async def retry(self, db: AsyncSession, user_id: int, kb_id: int, document_id: int) -> JobAccepted:
        await db.commit()
        try:
            kb = await require_owned_knowledge_base(db, kb_id, user_id, lock=True)
            self._ensure_writable(kb)
            document = await self._document(db, user_id, kb_id, document_id)
            if document.status == "deleting":
                raise KnowledgeBaseError("document_not_found", "文档已开始删除", 404)
            active = await self._active_job(db, kb_id, document_id=document_id, operation="index")
            if active:
                await db.commit()
                return await self._notify(db, active)
            if kb.status in ("reindex_required", "failed") or await self._active_job(db, kb_id, operation="reindex"):
                raise KnowledgeBaseError("reindex_required", "请使用整库重建")
            if document.status != "failed":
                raise KnowledgeBaseError("document_not_failed", "只有失败文档可以重试")
            document.index_revision += 1
            document.status, document.error_code = "queued", None
            kb.content_revision += 1
            kb.status, kb.error_code = "indexing", None
            job = await self._job(db, kb, "index", document)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return await self._notify(db, job)

    async def reindex(self, db: AsyncSession, user_id: int, kb_id: int) -> JobAccepted:
        await db.commit()
        try:
            kb = await require_owned_knowledge_base(db, kb_id, user_id, lock=True)
            self._ensure_writable(kb)
            active = await self._active_job(db, kb_id, operation="reindex")
            if active:
                await db.commit()
                return await self._notify(db, active)
            if await self._active_job(db, kb_id):
                raise KnowledgeBaseError("knowledge_base_busy", "请等待当前处理任务完成后重建")
            documents = (await db.scalars(select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == kb_id, KnowledgeDocument.status.not_in(("deleting", "deleted")),
            ))).all()
            if not documents:
                raise KnowledgeBaseError("knowledge_base_empty", "空知识库无需重建")
            kb.content_revision += 1
            kb.status, kb.error_code = "indexing", None
            for document in documents:
                document.index_revision += 1
                document.status, document.error_code = "queued", None
            job = await self._job(db, kb, "reindex")
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return await self._notify(db, job)

    async def delete_document(self, db: AsyncSession, user_id: int, kb_id: int, document_id: int) -> JobAccepted:
        await db.commit()
        try:
            kb = await require_owned_knowledge_base(db, kb_id, user_id, lock=True)
            self._ensure_writable(kb)
            document = await self._document(db, user_id, kb_id, document_id, include_deleted=True)
            if document.status not in ("deleting", "deleted"):
                kb.content_revision += 1
                if kb.status not in ("reindex_required", "failed"):
                    kb.status = "indexing"
                document.index_revision += 1
                document.status, document.error_code = "deleting", None
            job = await self._job(db, kb, "delete_document", document)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return await self._notify(db, job)

    async def delete(self, db: AsyncSession, user_id: int, kb_id: int) -> JobAccepted:
        await db.commit()
        try:
            kb = await require_owned_knowledge_base(db, kb_id, user_id, lock=True, include_deleted=True)
            if kb.status not in ("deleting", "deleted"):
                kb.content_revision += 1
                kb.status, kb.error_code = "deleting", None
                await db.execute(update(KnowledgeDocument).where(
                    KnowledgeDocument.knowledge_base_id == kb_id, KnowledgeDocument.status.not_in(("deleting", "deleted")),
                ).values(status="deleting", index_revision=KnowledgeDocument.index_revision + 1))
            job = await self._job(db, kb, "delete_knowledge_base")
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return await self._notify(db, job)


knowledge_base_service = KnowledgeBaseService()
