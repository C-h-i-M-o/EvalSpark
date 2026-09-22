"""为同轮失败分支重试增加生成代次，阻止旧进程收尾新尝试。"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_01"
down_revision = "20260920_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保留已有轮次，首次生成统一使用代次一。"""
    op.add_column("conversation_turns", sa.Column("generation_epoch", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    """移除代次字段，降级前必须停止使用失败分支重试。"""
    op.drop_column("conversation_turns", "generation_epoch")
