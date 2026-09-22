"""add Harbor profiles and pin profile snapshots to operations"""

import sqlalchemy as sa

from alembic import op

revision = "0009_harbor_profiles"
down_revision = "0008_artifact_destination_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harbor_profiles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("username", sa.String(256), nullable=True),
        sa.Column("verify_tls", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_harbor_profiles_name"),
    )
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("harbor_profile_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("harbor_profile_name", sa.String(128), nullable=True))
        batch.add_column(sa.Column("harbor_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_column("harbor_url")
        batch.drop_column("harbor_profile_name")
        batch.drop_column("harbor_profile_id")
    op.drop_table("harbor_profiles")
