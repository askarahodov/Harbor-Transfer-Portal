"""persist runtime mode snapshot on operations"""

import sqlalchemy as sa

from alembic import op

revision = "0007_runtime_mode_operation_snapshot"
down_revision = "0006_import_orchestration_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("runtime_mode", sa.String(16), nullable=True))
        batch.add_column(sa.Column("runtime_mode_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("runtime_mode_version")
        batch.drop_column("runtime_mode")
