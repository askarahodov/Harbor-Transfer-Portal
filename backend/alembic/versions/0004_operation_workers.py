"""add persistent operation worker state"""

import sqlalchemy as sa

from alembic import op

revision = "0004_operation_workers"
down_revision = "0003_harbor_settings_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(
            sa.Column(
                "conflict_artifacts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("worker_token", sa.String(64), nullable=True))
        batch.add_column(sa.Column("worker_started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_operations_worker_token", ["worker_token"], unique=False)
        batch.alter_column("conflict_artifacts", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_index("ix_operations_worker_token")
        batch.drop_column("cancel_requested_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("worker_started_at")
        batch.drop_column("worker_token")
        batch.drop_column("conflict_artifacts")
