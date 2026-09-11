"""persist login throttling state"""

import sqlalchemy as sa

from alembic import op

revision = "0002_login_throttles"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("scope", "subject_hash", name="uq_login_throttles_scope_subject_hash"),
        sa.CheckConstraint(
            "failure_count >= 0", name="ck_login_throttles_failure_count_nonnegative"
        ),
    )
    op.create_index(
        "ix_login_throttles_locked_until",
        "login_throttles",
        ["locked_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_login_throttles_locked_until", table_name="login_throttles")
    op.drop_table("login_throttles")
