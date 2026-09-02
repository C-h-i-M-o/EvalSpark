from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EvaluationTask(Base):
    __tablename__ = "evaluation_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    task_type: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat")
    visibility: Mapped[str] = mapped_column(String(16), default="public")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="tasks")
    user = relationship("User", back_populates="tasks")
    responses = relationship("ModelResponse", back_populates="task")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("model_responses.id"))
    relevance_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    completeness_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    clarity_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    format_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    safety_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    accuracy_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    usefulness_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    objective_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    rule_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=0)
    judge_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    score_status: Mapped[str] = mapped_column(String(32), default="scored")
    excluded_from_stats: Mapped[bool] = mapped_column(Boolean, default=False)
    judge_score_range: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    judge_runs_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    judge_prompt_group_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    judge_prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_dictionary_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    judge_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    response = relationship("ModelResponse", back_populates="evaluation_result")
