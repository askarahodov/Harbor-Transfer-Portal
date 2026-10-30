"""add registry_type to Harbor profiles for Nexus support"""

import sqlalchemy as sa

from alembic import op

revision = "0011_registry_type_for_profiles"
down_revision = "0010_operation_harbor_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT value FROM setting_metadata WHERE key = 'harbor.profiles.v1'")
    ).fetchone()

    if result and result[0]:
        import json
        profiles = json.loads(result[0])
        for profile in profiles:
            if 'registry_type' not in profile:
                profile['registry_type'] = 'harbor'
        updated_json = json.dumps(profiles)
        bind.execute(
            sa.text("UPDATE setting_metadata SET value = :val WHERE key = 'harbor.profiles.v1'"),
            {"val": updated_json},
        )


def downgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT value FROM setting_metadata WHERE key = 'harbor.profiles.v1'")
    ).fetchone()

    if result and result[0]:
        import json
        profiles = json.loads(result[0])
        for profile in profiles:
            profile.pop('registry_type', None)
        updated_json = json.dumps(profiles)
        bind.execute(
            sa.text("UPDATE setting_metadata SET value = :val WHERE key = 'harbor.profiles.v1'"),
            {"val": updated_json},
        )