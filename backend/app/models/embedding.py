"""管理员维护的全系统 Embedding 单例配置。"""

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmbeddingConfig(Base):
    __tablename__ = "embedding_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(default=0)
    # 凭据沿用现有服务端存储机制，API 读取必须显式排除。
    settings_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
