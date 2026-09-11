"""initial persistent schema"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.Enum("ADMIN", "OPERATOR", "VIEWER", name="userrole", native_enum=False), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)

    op.create_table(
        "operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.String(96), nullable=True),
        sa.Column("type", sa.Enum("EXPORT", "IMPORT", name="operationtype", native_enum=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED", "VALIDATING", "RUNNING", "PACKAGING", "VERIFYING", "UPLOADED",
                "DISCOVERED", "READY", "IMPORTING", "VERIFYING_TARGET", "COMPLETED", "FAILED",
                "REJECTED", "CANCELLED", name="operationstatus", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(128), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("total_artifacts", sa.Integer(), nullable=False),
        sa.Column("successful_artifacts", sa.Integer(), nullable=False),
        sa.Column("failed_artifacts", sa.Integer(), nullable=False),
        sa.Column("skipped_artifacts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_operations_actor_user_id_users", ondelete="SET NULL"),
        sa.UniqueConstraint("delivery_id", name="uq_operations_delivery_id"),
        sa.CheckConstraint("progress_current >= 0", name="ck_operations_progress_current_nonnegative"),
        sa.CheckConstraint("progress_total >= 0", name="ck_operations_progress_total_nonnegative"),
    )
    op.create_index("ix_operations_actor_user_id", "operations", ["actor_user_id"], unique=False)

    op.create_table(
        "artifact_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("repository", sa.String(512), nullable=False),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("reference", sa.String(256), nullable=True),
        sa.Column("version", sa.String(256), nullable=True),
        sa.Column("source_digest", sa.String(128), nullable=True),
        sa.Column("target_digest", sa.String(128), nullable=True),
        sa.Column("status", sa.Enum("PENDING", "RUNNING", "IMPORTED", "SKIPPED", "CONFLICT", "FAILED", "VERIFIED", name="artifactstatus", native_enum=False), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], name="fk_artifact_results_operation_id_operations", ondelete="CASCADE"),
    )
    op.create_index("ix_artifact_results_operation_id", "artifact_results", ["operation_id"], unique=False)

    op.create_table(
        "setting_metadata",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("setting_metadata")
    op.drop_index("ix_artifact_results_operation_id", table_name="artifact_results")
    op.drop_table("artifact_results")
    op.drop_index("ix_operations_actor_user_id", table_name="operations")
    op.drop_table("operations")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
