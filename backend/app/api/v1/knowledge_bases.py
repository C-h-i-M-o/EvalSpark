from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.knowledge_base import (
    JobAccepted, KnowledgeBaseCreate, KnowledgeBaseList, KnowledgeBasePatch,
    KnowledgeBaseRead, KnowledgeDocumentList,
)
from app.services.knowledge_base_service import knowledge_base_service, require_owned_knowledge_base
from app.services.rag.documents import read_multipart_upload

router = APIRouter()
Database = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
PositiveId = Annotated[int, Path(gt=0)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(alias="pageSize", ge=1, le=100)]


@router.post("", response_model=KnowledgeBaseRead, status_code=201)
async def create_knowledge_base(payload: KnowledgeBaseCreate, db: Database, user: CurrentUser) -> KnowledgeBaseRead:
    return await knowledge_base_service.create(db, user.id, payload)


@router.get("", response_model=KnowledgeBaseList)
async def list_knowledge_bases(db: Database, user: CurrentUser, page: Page = 1, page_size: PageSize = 20) -> KnowledgeBaseList:
    return await knowledge_base_service.list(db, user.id, page, page_size)


@router.get("/{kb_id}", response_model=KnowledgeBaseRead)
async def get_knowledge_base(kb_id: PositiveId, db: Database, user: CurrentUser) -> KnowledgeBaseRead:
    return await knowledge_base_service.get(db, user.id, kb_id)


@router.patch("/{kb_id}", response_model=KnowledgeBaseRead)
async def patch_knowledge_base(kb_id: PositiveId, payload: KnowledgeBasePatch, db: Database, user: CurrentUser) -> KnowledgeBaseRead:
    return await knowledge_base_service.patch(db, user.id, kb_id, payload)


@router.delete("/{kb_id}", response_model=JobAccepted, status_code=202)
async def delete_knowledge_base(kb_id: PositiveId, db: Database, user: CurrentUser) -> JobAccepted:
    return await knowledge_base_service.delete(db, user.id, kb_id)


@router.post("/{kb_id}/reindex", response_model=JobAccepted, status_code=202)
async def reindex_knowledge_base(kb_id: PositiveId, db: Database, user: CurrentUser) -> JobAccepted:
    return await knowledge_base_service.reindex(db, user.id, kb_id)


@router.get("/{kb_id}/documents", response_model=KnowledgeDocumentList)
async def list_documents(kb_id: PositiveId, db: Database, user: CurrentUser, page: Page = 1, page_size: PageSize = 20) -> KnowledgeDocumentList:
    return await knowledge_base_service.list_documents(db, user.id, kb_id, page, page_size)


@router.post("/{kb_id}/documents", response_model=JobAccepted, status_code=202, openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"], "additionalProperties": False,
        "properties": {"file": {"type": "string", "format": "binary"}},
    }}}},
})
async def upload_document(request: Request, kb_id: PositiveId, db: Database, user: CurrentUser) -> JobAccepted:
    # 先检查归属再读取请求体，不让未授权的大文件触发 multipart 磁盘写入。
    user_id = user.id
    await require_owned_knowledge_base(db, kb_id, user_id)
    await db.commit()
    async with read_multipart_upload(request) as upload:
        return await knowledge_base_service.upload(db, user_id, kb_id, upload)


@router.post("/{kb_id}/documents/{document_id}/retry", response_model=JobAccepted, status_code=202)
async def retry_document(kb_id: PositiveId, document_id: PositiveId, db: Database, user: CurrentUser) -> JobAccepted:
    return await knowledge_base_service.retry(db, user.id, kb_id, document_id)


@router.delete("/{kb_id}/documents/{document_id}", response_model=JobAccepted, status_code=202)
async def delete_document(kb_id: PositiveId, document_id: PositiveId, db: Database, user: CurrentUser) -> JobAccepted:
    return await knowledge_base_service.delete_document(db, user.id, kb_id, document_id)


@router.get("/{kb_id}/documents/{document_id}/download", response_class=FileResponse)
async def download_document(kb_id: PositiveId, document_id: PositiveId, db: Database, user: CurrentUser) -> FileResponse:
    path, name, media_type = await knowledge_base_service.download(db, user.id, kb_id, document_id)
    return FileResponse(path, filename=name, media_type=media_type, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
    })
