from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RagResponseDetail(Base):
    __tablename__ = "rag_response_details"

    response_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("model_responses.id"), primary_key=True)
    # 历史来源是独立快照，不建立知识库/文档外键，删除当前资料不级联历史证据。
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger)
    knowledge_base_name: Mapped[str] = mapped_column(String(120))
    content_revision: Mapped[int] = mapped_column()
    embedding_revision: Mapped[str] = mapped_column(String(40))
    chunk_size: Mapped[int] = mapped_column()
    chunk_overlap: Mapped[int] = mapped_column()
    document_versions_json: Mapped[list[dict[str, int]]] = mapped_column(JSON, default=list)
    rewritten_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    stage_usage_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    judge_runs_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    faithfulness: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    citation_correctness: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    citation_completeness: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    rag_final: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    base_final: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    score_version: Mapped[str] = mapped_column(String(32), default="rag-v1")
    failure_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
