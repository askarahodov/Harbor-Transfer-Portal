"""persist immutable Harbor profile snapshot on operations"""

import sqlalchemy as sa

from alembic import op

revision = "0010_operation_harbor_profile"
down_revision = "0009_export_handoff_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("harbor_profile_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("harbor_profile_name", sa.String(128), nullable=True))
        batch.add_column(sa.Column("harbor_profile_url", sa.String(2048), nullable=True))
        batch.create_index(
            "ix_operations_harbor_profile_id",
            ["harbor_profile_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_index("ix_operations_harbor_profile_id")
        batch.drop_column("harbor_profile_url")
        batch.drop_column("harbor_profile_name")
        batch.drop_column("harbor_profile_id")
