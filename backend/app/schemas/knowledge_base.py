from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pydantic.alias_generators import to_camel

KnowledgeBaseStatus = Literal["empty", "indexing", "ready", "reindex_required", "failed", "deleting", "deleted"]
DocumentStatus = Literal["queued", "parsing", "embedding", "indexing", "ready", "failed", "deleting", "deleted"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobOperation = Literal["index", "reindex", "delete_document", "delete_knowledge_base"]


class KnowledgeSchema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", from_attributes=True)


class KnowledgeBaseCreate(KnowledgeSchema):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    chunk_size: StrictInt = Field(default=800, ge=128, le=2048)
    chunk_overlap: StrictInt = Field(default=120, ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("重叠 Token 数必须小于切块大小")
        return self


class KnowledgeBasePatch(KnowledgeSchema):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    chunk_size: StrictInt | None = Field(default=None, ge=128, le=2048)
    chunk_overlap: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def reject_explicit_null(self) -> Self:
        for field in ("name", "chunk_size", "chunk_overlap"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("名称和切分参数不能为 null")
        return self


class TextSource(KnowledgeSchema):
    kind: Literal["text"] = "text"
    line_start: StrictInt = Field(ge=1)
    line_end: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("来源行号区间无效")
        return self


class PdfSource(KnowledgeSchema):
    kind: Literal["pdf"] = "pdf"
    page_start: StrictInt = Field(ge=1)
    page_end: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("来源页码区间无效")
        return self


class DocxSource(KnowledgeSchema):
    kind: Literal["docx"] = "docx"
    block_start: StrictInt = Field(ge=1)
    block_end: StrictInt = Field(ge=1)
    block_type: Literal["paragraph", "table", "mixed"]

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.block_end < self.block_start:
            raise ValueError("来源块区间无效")
        return self


SourceLocation = Annotated[TextSource | PdfSource | DocxSource, Field(discriminator="kind")]


class RagJobRead(KnowledgeSchema):
    id: str
    knowledge_base_id: int
    document_id: int | None
    operation: JobOperation
    target_revision: int
    status: JobStatus
    stage: str
    processed_count: int
    total_count: int
    attempt: int
    error_code: str | None
    dispatch_pending: bool = False
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseRead(KnowledgeSchema):
    id: int
    name: str
    description: str | None
    chunk_size: int
    chunk_overlap: int
    status: KnowledgeBaseStatus
    content_revision: int
    document_count: int
    chunk_count: int
    available: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentRead(KnowledgeSchema):
    id: int
    knowledge_base_id: int
    original_name: str
    media_type: str
    size_bytes: int
    status: DocumentStatus
    index_revision: int
    chunk_count: int
    error_code: str | None
    current_job: RagJobRead | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseList(KnowledgeSchema):
    items: list[KnowledgeBaseRead]
    total: int
    page: int
    page_size: int


class KnowledgeDocumentList(KnowledgeSchema):
    items: list[KnowledgeDocumentRead]
    total: int
    page: int
    page_size: int


class JobAccepted(KnowledgeSchema):
    document_id: int | None
    job_id: str
    status: JobStatus
    dispatch_pending: bool
