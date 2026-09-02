from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utc_now() -> datetime:
    # MySQL DATETIME 不携带时区，保持项目既有的 UTC 存储约定。
    return datetime.now(UTC).replace(tzinfo=None)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (Index("ix_knowledge_bases_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_size: Mapped[int] = mapped_column(default=800)
    chunk_overlap: Mapped[int] = mapped_column(default=120)
    status: Mapped[str] = mapped_column(String(32), default="empty")
    content_revision: Mapped[int] = mapped_column(default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_documents_library_status", "knowledge_base_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    media_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    index_revision: Mapped[int] = mapped_column(default=1)
    chunk_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "index_revision", "chunk_index", name="uq_knowledge_chunk_version"),
        Index("ix_knowledge_chunks_scope", "user_id", "knowledge_base_id", "document_id", "index_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("knowledge_documents.id"))
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    index_revision: Mapped[int] = mapped_column()
    chunk_index: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column()
    source_json: Mapped[dict[str, object]] = mapped_column(JSON)


class RagJob(Base):
    __tablename__ = "rag_jobs"
    __table_args__ = (
        Index("ix_rag_jobs_library_status", "knowledge_base_id", "status"),
        Index("ix_rag_jobs_recovery", "status", "lease_until", "dispatched_at"),
        Index("ix_rag_jobs_document", "document_id", "created_at"),
    )

    # ID 由操作目标与版本确定，消息重投不会创建第二份同版本作业。
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("knowledge_bases.id"))
    document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("knowledge_documents.id"), nullable=True)
    operation: Mapped[str] = mapped_column(String(32))
    target_revision: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    processed_count: Mapped[int] = mapped_column(default=0)
    total_count: Mapped[int] = mapped_column(default=0)
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)
