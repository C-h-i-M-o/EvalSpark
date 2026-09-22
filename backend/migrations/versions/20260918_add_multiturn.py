"""增加多轮会话版本、轮次和评审流水，保留全部旧任务与评分。"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_01"
down_revision = "20260909_01"
branch_labels = None
depends_on = None


def _add_conversation_column(column: sa.Column) -> None:
    """MySQL DDL 非事务化；允许从已核实的相同列结构继续未完成升级。"""
    existing = {item["name"]: item for item in sa.inspect(op.get_bind()).get_columns("conversations")}
    if column.name in existing:
        current = existing[column.name]
        dialect = op.get_bind().dialect
        if str(current["type"].compile(dialect=dialect)).lower() != str(column.type.compile(dialect=dialect)).lower() or current["nullable"] != column.nullable:
            raise ValueError("已有会话列结构与多轮迁移不一致，请先检查数据库")
        return
    op.add_column("conversations", column)


def upgrade() -> None:
    """仅扩展结构，不调用模型、不迁移历史任务为新会话。"""
    _add_conversation_column(sa.Column("visibility", sa.String(16), nullable=False, server_default="private"))
    _add_conversation_column(sa.Column("config_json", sa.JSON(), nullable=True))
    _add_conversation_column(sa.Column("knowledge_snapshot_json", sa.JSON(), nullable=True))
    _add_conversation_column(sa.Column("current_turn", sa.Integer(), nullable=False, server_default="0"))
    _add_conversation_column(sa.Column("generation_status", sa.String(24), nullable=False, server_default="idle"))
    _add_conversation_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.create_table("conversation_turns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("evaluation_tasks.id"), nullable=False, unique=True),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("generation_status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("conversation_id", "turn_index", name="uq_conversation_turn_index"),
        sa.UniqueConstraint("conversation_id", "request_key", name="uq_conversation_request_key"))
    op.create_index("ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"])
    op.create_table("conversation_contexts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("turn_id", sa.BigInteger(), sa.ForeignKey("conversation_turns.id"), nullable=False),
        sa.Column("model_config_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("covered_through_turn", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("turn_id", "model_config_id", "attempt", name="uq_conversation_context_attempt"))
    op.create_index("ix_conversation_contexts_conversation_id", "conversation_contexts", ["conversation_id"])
    op.create_table("conversation_requirements",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("requirement_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_turn", sa.Integer(), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversation_id", "requirement_key", "version", name="uq_conversation_requirement_version"))
    op.create_index("ix_conversation_requirements_conversation_id", "conversation_requirements", ["conversation_id"])
    op.create_table("conversation_assessments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("response_id", sa.BigInteger(), sa.ForeignKey("model_responses.id"), nullable=True),
        sa.Column("model_config_id", sa.BigInteger(), nullable=False),
        sa.Column("through_turn", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("formal", sa.Boolean(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("conversation_id", "operation_key", name="uq_conversation_assessment_operation"))
    op.create_index("ix_conversation_assessments_conversation_id", "conversation_assessments", ["conversation_id"])
    op.create_index("ix_conversation_assessments_status", "conversation_assessments", ["status"])
    op.create_table("conversation_judge_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("assessment_id", sa.BigInteger(), sa.ForeignKey("conversation_assessments.id"), nullable=False),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("assessment_id", "run_index", name="uq_conversation_judge_run"))
    op.create_table("conversation_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation_key", sa.String(96), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cost", sa.Numeric(18, 10), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("accounted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversation_id", "operation_key", name="uq_conversation_usage_operation"))
    op.create_index("ix_conversation_usage_conversation_id", "conversation_usage", ["conversation_id"])


def downgrade() -> None:
    """仅用于明确授权的回滚；回滚会删除新增会话明细，必须先备份。"""
    for table in ("conversation_usage", "conversation_judge_runs", "conversation_assessments",
                  "conversation_requirements", "conversation_contexts", "conversation_turns"):
        op.drop_table(table)
    for column in ("updated_at", "generation_status", "current_turn", "knowledge_snapshot_json", "config_json", "visibility"):
        op.drop_column("conversations", column)
