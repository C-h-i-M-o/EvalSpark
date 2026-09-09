"""增加全局 Embedding 配置，不修改历史知识库或向量。"""

from alembic import op
import sqlalchemy as sa

revision = "20260909_01"
down_revision = "20260902_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table("embedding_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False))
    # 空配置保留历史本地路径；管理员显式保存后才启用新接口。
    op.bulk_insert(table, [{"id": 1, "version": 0, "settings_json": {}}])


def downgrade() -> None:
    op.drop_table("embedding_config")
