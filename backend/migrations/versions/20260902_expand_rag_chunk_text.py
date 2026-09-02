"""扩大 Token 合规但字节数超过 64 KiB 的文本块存储，不截断旧数据。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision: str = "20260902_02"
down_revision: str | None = "20260902_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("knowledge_chunks", "text", existing_type=sa.Text(), type_=MEDIUMTEXT(), existing_nullable=False)


def downgrade() -> None:
    raise RuntimeError("禁止自动缩小文本块字段；请按经批准的备份恢复流程处理")
