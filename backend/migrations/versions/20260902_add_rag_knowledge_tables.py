"""新增私有知识库与 RAG 独立证据表，保留普通任务和历史得分。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260902_01"
down_revision: str | None = "20260705_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("evaluation_tasks", sa.Column("task_type", sa.String(16), nullable=False, server_default="chat"))
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="800"),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("status", sa.String(32), nullable=False, server_default="empty"),
        sa.Column("content_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_knowledge_bases_user_status", "knowledge_bases", ["user_id", "status"])
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("knowledge_base_id", sa.BigInteger(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False, unique=True),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("index_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_knowledge_documents_library_status", "knowledge_documents", ["knowledge_base_id", "status"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.BigInteger(), sa.ForeignKey("knowledge_documents.id"), nullable=False),
        sa.Column("knowledge_base_id", sa.BigInteger(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("index_revision", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("document_id", "index_revision", "chunk_index", name="uq_knowledge_chunk_version"),
    )
    op.create_index("ix_knowledge_chunks_scope", "knowledge_chunks", ["user_id", "knowledge_base_id", "document_id", "index_revision"])
    op.create_table(
        "rag_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_base_id", sa.BigInteger(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("document_id", sa.BigInteger(), sa.ForeignKey("knowledge_documents.id"), nullable=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_rag_jobs_library_status", "rag_jobs", ["knowledge_base_id", "status"])
    op.create_index("ix_rag_jobs_recovery", "rag_jobs", ["status", "lease_until", "dispatched_at"])
    op.create_index("ix_rag_jobs_document", "rag_jobs", ["document_id", "created_at"])
    op.create_table(
        "rag_response_details",
        sa.Column("response_id", sa.BigInteger(), sa.ForeignKey("model_responses.id"), primary_key=True),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_base_name", sa.String(120), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("embedding_revision", sa.String(40), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("document_versions_json", sa.JSON(), nullable=False),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("stage_usage_json", sa.JSON(), nullable=False),
        sa.Column("judge_runs_json", sa.JSON(), nullable=False),
        sa.Column("faithfulness", sa.Numeric(18, 10), nullable=True),
        sa.Column("citation_correctness", sa.Numeric(18, 10), nullable=True),
        sa.Column("citation_completeness", sa.Numeric(18, 10), nullable=True),
        sa.Column("rag_final", sa.Numeric(18, 10), nullable=True),
        sa.Column("base_final", sa.Numeric(18, 10), nullable=True),
        sa.Column("score_version", sa.String(32), nullable=False, server_default="rag-v1"),
        sa.Column("failure_stage", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("RAG 数据迁移不支持自动降级；请使用经确认的备份恢复流程")
