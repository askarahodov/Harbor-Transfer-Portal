"""first-run password rotation and revocable auth sessions"""

import sqlalchemy as sa

from alembic import op

revision = "0012_first_run_admin"
down_revision = "0011_registry_type_for_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "must_change_password", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
        batch.drop_column("must_change_password")
