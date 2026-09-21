"""persist signed physical handoff metadata"""

import sqlalchemy as sa

from alembic import op

revision = "0009_export_handoff_metadata"
down_revision = "0008_artifact_destination_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("handoff_filename", sa.String(192), nullable=True))
        batch.add_column(sa.Column("handoff_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("handoff_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("handoff_size_bytes")
        batch.drop_column("handoff_sha256")
        batch.drop_column("handoff_filename")
