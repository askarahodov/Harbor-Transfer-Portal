"""persist TARGET import orchestration metadata"""

import sqlalchemy as sa

from alembic import op

revision = "0006_import_orchestration_metadata"
down_revision = "0005_export_bundle_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("import_storage_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("import_intake_mode", sa.String(16), nullable=True))
        batch.add_column(sa.Column("source_delivery_id", sa.String(96), nullable=True))
        batch.add_column(
            sa.Column("bundle_signing_key_fingerprint", sa.String(64), nullable=True)
        )
        batch.add_column(sa.Column("import_preview_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("import_policy_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("import_receipt_json", sa.Text(), nullable=True))
        batch.create_index("ix_operations_source_delivery_id", ["source_delivery_id"])


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_index("ix_operations_source_delivery_id")
        batch.drop_column("import_receipt_json")
        batch.drop_column("import_policy_json")
        batch.drop_column("import_preview_json")
        batch.drop_column("bundle_signing_key_fingerprint")
        batch.drop_column("source_delivery_id")
        batch.drop_column("import_intake_mode")
        batch.drop_column("import_storage_key")
