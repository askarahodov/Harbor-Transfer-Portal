"""add retry linkage and remaining artifact mapping fields

Revision ID: 0008_import_retry_semantics
Revises: 0008_artifact_destination_snapshot
Create Date: 2026-09-15 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0008_import_retry_semantics"
down_revision = "0008_artifact_destination_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- operations table ---
    ops_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info(operations)"))}
    if "retry_of_operation_id" not in ops_cols:
        op.add_column("operations", sa.Column("retry_of_operation_id", sa.Integer(), nullable=True))
    if "destination_plan_id" not in ops_cols:
        op.add_column("operations", sa.Column("destination_plan_id", sa.String(96), nullable=True))
    if "destination_plan_hash" not in ops_cols:
        op.add_column("operations", sa.Column("destination_plan_hash", sa.String(128), nullable=True))
    if "failure_policy" not in ops_cols:
        op.add_column("operations", sa.Column("failure_policy", sa.String(64), nullable=True))

    # --- artifact_results table ---
    art_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info(artifact_results)"))}
    if "source_digest" not in art_cols:
        op.add_column("artifact_results", sa.Column("source_digest", sa.String(128), nullable=True))
    if "target_digest" not in art_cols:
        op.add_column("artifact_results", sa.Column("target_digest", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("artifact_results", "target_digest")
    op.drop_column("artifact_results", "source_digest")
    op.drop_column("operations", "failure_policy")
    op.drop_column("operations", "destination_plan_hash")
    op.drop_column("operations", "destination_plan_id")
    op.drop_column("operations", "retry_of_operation_id")