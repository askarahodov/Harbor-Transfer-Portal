"""add retry linkage and destination mapping fields to operations and artifact_results

Revision ID: 0008_import_retry_semantics
Revises: 0007_runtime_mode_operation_snapshot
Create Date: 2026-09-15 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0008_import_retry_semantics"
down_revision = "0007_runtime_mode_operation_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- operations table ---
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("retry_of_operation_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("destination_plan_id", sa.String(96), nullable=True))
        batch.add_column(sa.Column("destination_plan_hash", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column("failure_policy", sa.String(16), nullable=True, server_default="continue")
        )

    # FK constraint for retry_of_operation_id (self-referential)
    op.create_foreign_key(
        "fk_operations_retry_of",
        "operations",
        "operations",
        ["retry_of_operation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- artifact_results table ---
    with op.batch_alter_table("artifact_results") as batch:
        batch.add_column(sa.Column("source_project", sa.String(255), nullable=True))
        batch.add_column(sa.Column("source_repository", sa.String(512), nullable=True))
        batch.add_column(sa.Column("source_reference", sa.String(256), nullable=True))
        batch.add_column(sa.Column("source_digest", sa.String(128), nullable=True))
        batch.add_column(sa.Column("target_project", sa.String(255), nullable=True))
        batch.add_column(sa.Column("target_repository", sa.String(512), nullable=True))
        batch.add_column(sa.Column("target_reference", sa.String(256), nullable=True))
        batch.add_column(sa.Column("target_digest", sa.String(128), nullable=True))
        batch.add_column(sa.Column("destination_plan_id", sa.String(96), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifact_results") as batch:
        batch.drop_column("destination_plan_id")
        batch.drop_column("target_digest")
        batch.drop_column("target_reference")
        batch.drop_column("target_repository")
        batch.drop_column("target_project")
        batch.drop_column("source_digest")
        batch.drop_column("source_reference")
        batch.drop_column("source_repository")
        batch.drop_column("source_project")

    op.drop_constraint("fk_operations_retry_of", "operations", type_="foreignkey")

    with op.batch_alter_table("operations") as batch:
        batch.drop_column("failure_policy")
        batch.drop_column("destination_plan_hash")
        batch.drop_column("destination_plan_id")
        batch.drop_column("retry_of_operation_id")
