"""pin Harbor profile snapshot to transfer operations"""

import sqlalchemy as sa

from alembic import op

revision = "0010_harbor_profile_binding"
down_revision = "0009_export_handoff_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("harbor_profile_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("harbor_profile_name", sa.String(128), nullable=True))
        batch.add_column(sa.Column("harbor_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("harbor_url")
        batch.drop_column("harbor_profile_name")
        batch.drop_column("harbor_profile_id")
