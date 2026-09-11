"""persist export bundle metadata"""

import sqlalchemy as sa

from alembic import op

revision = "0005_export_bundle_metadata"
down_revision = "0004_operation_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("bundle_filename", sa.String(192), nullable=True))
        batch.add_column(sa.Column("bundle_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("bundle_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("bundle_size_bytes")
        batch.drop_column("bundle_sha256")
        batch.drop_column("bundle_filename")
