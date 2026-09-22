"""新增管理员声明的模型总上下文容量，旧配置保持未知。"""
from alembic import op
import sqlalchemy as sa

revision = "20260920_01"
down_revision = "20260918_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """添加可空容量字段，不猜测或修改现有配置容量。"""
    op.add_column("model_configs", sa.Column("context_window", sa.Integer(), nullable=True))


def downgrade() -> None:
    """回退仅移除本次容量字段。"""
    op.drop_column("model_configs", "context_window")
