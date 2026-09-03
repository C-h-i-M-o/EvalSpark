from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ModelResponse(Base):
    __tablename__ = "model_responses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("evaluation_tasks.id"))
    model_config_id: Mapped[int | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    input_cost: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=0)
    output_cost: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=0)
    cache_hit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=0)
    cache_creation_cost: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    config_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task = relationship("EvaluationTask", back_populates="responses")
    model_config = relationship("ModelConfig", back_populates="responses")
    evaluation_result = relationship("EvaluationResult", back_populates="response", uselist=False)
    feedback = relationship("UserFeedback", back_populates="response")
    comments = relationship("UserComment", back_populates="response")
    rag_detail = relationship("RagResponseDetail", uselist=False)
