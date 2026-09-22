from datetime import datetime

from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120), default="新评测会话")
    mode: Mapped[str] = mapped_column(String(32), default="compare")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    visibility: Mapped[str] = mapped_column(String(16), default="private", server_default="private")
    config_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    knowledge_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    current_turn: Mapped[int] = mapped_column(default=0, server_default="0")
    generation_status: Mapped[str] = mapped_column(String(24), default="idle", server_default="idle")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="conversations")
    tasks = relationship("EvaluationTask", back_populates="conversation")


class ConversationTurn(Base):
    """以唯一轮次和请求键持久化一次共同问题，生成状态独立于评分。"""

    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "turn_index", name="uq_conversation_turn_index"),
        UniqueConstraint("conversation_id", "request_key", name="uq_conversation_request_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), index=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("evaluation_tasks.id"), unique=True)
    turn_index: Mapped[int] = mapped_column()
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    generation_epoch: Mapped[int] = mapped_column(default=1, server_default="1")
    prompt: Mapped[str] = mapped_column(Text)
    generation_status: Mapped[str] = mapped_column(String(24), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConversationContext(Base):
    """保存分支上下文版本及原文来源，不以摘要覆盖原始消息。"""

    __tablename__ = "conversation_contexts"
    __table_args__ = (UniqueConstraint("turn_id", "model_config_id", "attempt", name="uq_conversation_context_attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), index=True)
    turn_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversation_turns.id"))
    model_config_id: Mapped[int] = mapped_column(BigInteger)
    attempt: Mapped[int] = mapped_column(default=1)
    covered_through_turn: Mapped[int] = mapped_column(default=0)
    source_hash: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversationRequirement(Base):
    """记录用户要求的版本和来源，避免后续修正污染过去轮次的评分。"""

    __tablename__ = "conversation_requirements"
    __table_args__ = (UniqueConstraint("conversation_id", "requirement_key", "version", name="uq_conversation_requirement_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), index=True)
    requirement_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column()
    source_turn: Mapped[int] = mapped_column()
    detail_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversationAssessment(Base):
    """评分作业及结果快照；同一输入与评分范围只调度一次。"""

    __tablename__ = "conversation_assessments"
    __table_args__ = (UniqueConstraint("conversation_id", "operation_key", name="uq_conversation_assessment_operation"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), index=True)
    response_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("model_responses.id"), nullable=True)
    model_config_id: Mapped[int] = mapped_column(BigInteger)
    through_turn: Mapped[int] = mapped_column()
    operation_key: Mapped[str] = mapped_column(String(64))
    score_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    formal: Mapped[bool] = mapped_column(default=False)
    input_json: Mapped[dict[str, object]] = mapped_column(JSON)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConversationJudgeRun(Base):
    """每次评审独立保存，失败也保留，防止复评覆盖历史。"""

    __tablename__ = "conversation_judge_runs"
    __table_args__ = (UniqueConstraint("assessment_id", "run_index", name="uq_conversation_judge_run"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversation_assessments.id"))
    run_index: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="pending")
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversationUsage(Base):
    """阶段用量的幂等流水，未知用量以空值记录而非伪造零。"""

    __tablename__ = "conversation_usage"
    __table_args__ = (UniqueConstraint("conversation_id", "operation_key", name="uq_conversation_usage_operation"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    operation_key: Mapped[str] = mapped_column(String(96))
    stage: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    total_tokens: Mapped[int | None] = mapped_column(nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    detail_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    accounted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
