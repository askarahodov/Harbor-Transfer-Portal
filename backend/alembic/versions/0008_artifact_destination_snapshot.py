"""persist source-to-target artifact mapping snapshots"""

import sqlalchemy as sa

from alembic import op

revision = "0008_artifact_destination_snapshot"
down_revision = "0007_runtime_mode_operation_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artifact_results") as batch:
        batch.add_column(sa.Column("source_project", sa.String(256), nullable=True))
        batch.add_column(sa.Column("source_repository", sa.String(512), nullable=True))
        batch.add_column(sa.Column("source_reference", sa.String(256), nullable=True))
        batch.add_column(sa.Column("source_version", sa.String(256), nullable=True))
        batch.add_column(sa.Column("target_project", sa.String(256), nullable=True))
        batch.add_column(sa.Column("target_repository", sa.String(512), nullable=True))
        batch.add_column(sa.Column("target_reference", sa.String(1024), nullable=True))
        batch.add_column(sa.Column("target_version", sa.String(256), nullable=True))
        batch.add_column(sa.Column("destination_plan_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("destination_plan_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("overwrite_approved", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifact_results") as batch:
        batch.drop_column("overwrite_approved")
        batch.drop_column("destination_plan_hash")
        batch.drop_column("destination_plan_id")
        batch.drop_column("target_version")
        batch.drop_column("target_reference")
        batch.drop_column("target_repository")
        batch.drop_column("target_project")
        batch.drop_column("source_version")
        batch.drop_column("source_reference")
        batch.drop_column("source_repository")
        batch.drop_column("source_project")
