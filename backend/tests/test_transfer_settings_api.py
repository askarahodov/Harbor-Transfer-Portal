import json
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from alembic import command
from app.api.exports import get_export_orchestrator
from app.api.imports import get_import_orchestrator
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_TEST_KEY = "transfer-settings-test-" + "x" * 32
MIB = 1024**2


def _app_client(
    tmp_path: Path,
    *,
    contour: PortalContour = PortalContour.TARGET,
) -> tuple[TestClient, object, dict[str, str], str]:
    database_url = f"sqlite:///{tmp_path / 'transfer-settings.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            portal_contour=contour,
            import_discovery_root=tmp_path / "incoming",
            import_staging_root=tmp_path / "staged",
            bundle_extract_root=tmp_path / "verified",
            operation_workspace_root=tmp_path / "operations",
        )
    )
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        for username, role in (
            ("admin", UserRole.ADMIN),
            ("operator", UserRole.OPERATOR),
            ("viewer", UserRole.VIEWER),
        ):
            if repo.get_by_username(username) is None:
                repo.create(
                    username=username,
                    password_hash=hash_password(f"{username}-test-passphrase"),
                    role=role,
                )
        session.commit()

    client = TestClient(app)
    tokens: dict[str, str] = {}
    for username in ("admin", "operator", "viewer"):
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": f"{username}-test-passphrase"},
        )
        assert response.status_code == 200
        tokens[username] = response.json()["access_token"]
    return client, app, tokens, database_url


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _policy_payload(*, concurrency: int = 4) -> dict[str, int | bool]:
    return {
        "import_allow_overwrite": True,
        "import_max_upload_bytes": 2 * MIB,
        "bundle_max_archive_bytes": 4 * MIB,
        "bundle_max_extracted_bytes": 8 * MIB,
        "bundle_max_member_count": 100,
        "operation_max_concurrent": concurrency,
    }


def test_transfer_settings_are_admin_only_with_safe_defaults(tmp_path: Path) -> None:
    client, _app, tokens, _database_url = _app_client(tmp_path)

    admin = client.get("/api/settings/transfer", headers=_auth(tokens["admin"]))
    assert admin.status_code == 200
    payload = admin.json()
    assert payload["import_allow_overwrite"] is False
    assert payload["operation_max_concurrent"] == 2
    assert payload["operation_max_concurrent_active"] == 2
    assert payload["restart_required_fields"] == []

    for role in ("operator", "viewer"):
        denied_get = client.get("/api/settings/transfer", headers=_auth(tokens[role]))
        denied_patch = client.patch(
            "/api/settings/transfer",
            json={"import_allow_overwrite": True},
            headers=_auth(tokens[role]),
        )
        assert denied_get.status_code == 403
        assert denied_patch.status_code == 403


def test_transfer_settings_apply_hot_fields_and_audit_safe_changes(tmp_path: Path) -> None:
    client, app, tokens, _database_url = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    updated = client.patch(
        "/api/settings/transfer",
        json=_policy_payload(),
        headers=headers,
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["import_allow_overwrite"] is True
    assert payload["import_max_upload_bytes"] == 2 * MIB
    assert payload["bundle_max_archive_bytes"] == 4 * MIB
    assert payload["bundle_max_extracted_bytes"] == 8 * MIB
    assert payload["bundle_max_member_count"] == 100
    assert payload["operation_max_concurrent"] == 4
    assert payload["operation_max_concurrent_active"] == 2
    assert payload["restart_required_fields"] == ["operation_max_concurrent"]

    assert app.state.settings.import_allow_overwrite is True
    assert app.state.settings.import_max_upload_bytes == 2 * MIB
    assert app.state.settings.bundle_max_archive_bytes == 4 * MIB
    assert app.state.settings.bundle_max_extracted_bytes == 8 * MIB
    assert app.state.settings.bundle_max_member_count == 100
    assert app.state.settings.operation_max_concurrent == 2

    request = Request({"type": "http", "app": app, "headers": []})
    assert get_import_orchestrator(request).settings.import_allow_overwrite is True
    assert get_import_orchestrator(request).settings.import_max_upload_bytes == 2 * MIB
    assert get_export_orchestrator(request).settings.bundle_max_archive_bytes == 4 * MIB

    with app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "transfer.settings.updated")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        metadata = json.loads(event.metadata_json)
    assert metadata["changed_fields"] == sorted(_policy_payload())
    assert metadata["changes"]["import_allow_overwrite"] == {
        "old": False,
        "new": True,
        "apply_mode": "hot",
    }
    assert metadata["changes"]["operation_max_concurrent"] == {
        "old": 2,
        "new": 4,
        "apply_mode": "restart_required",
    }


def test_transfer_settings_validate_cross_field_limits_atomically(tmp_path: Path) -> None:
    client, _app, tokens, _database_url = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    rejected = client.patch(
        "/api/settings/transfer",
        json={"bundle_max_archive_bytes": MIB},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "transfer_limits_inconsistent"

    unchanged = client.get("/api/settings/transfer", headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()["bundle_max_archive_bytes"] == 50 * 1024**3
    assert unchanged.json()["import_max_upload_bytes"] == 50 * 1024**3

    null_rejected = client.patch(
        "/api/settings/transfer",
        json={"import_allow_overwrite": None},
        headers=headers,
    )
    assert null_rejected.status_code == 422
    assert null_rejected.json()["error"]["code"] == "validation_error"


def test_browser_upload_limit_is_enforced_immediately_after_policy_change(tmp_path: Path) -> None:
    client, _app, tokens, _database_url = _app_client(tmp_path)
    admin_headers = _auth(tokens["admin"])

    updated = client.patch(
        "/api/settings/transfer",
        json={
            "import_max_upload_bytes": MIB,
            "bundle_max_archive_bytes": 2 * MIB,
            "bundle_max_extracted_bytes": 4 * MIB,
        },
        headers=admin_headers,
    )
    assert updated.status_code == 200

    response = client.post(
        "/api/imports/upload",
        content=b"x" * (MIB + 1),
        headers=_auth(tokens["operator"]),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "import_upload_too_large"


def test_restart_required_concurrency_is_applied_on_next_app_start(tmp_path: Path) -> None:
    client, _app, tokens, database_url = _app_client(tmp_path)
    updated = client.patch(
        "/api/settings/transfer",
        json={"operation_max_concurrent": 5},
        headers=_auth(tokens["admin"]),
    )
    assert updated.status_code == 200
    assert updated.json()["operation_max_concurrent_active"] == 2
    assert updated.json()["restart_required_fields"] == ["operation_max_concurrent"]

    restarted = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            portal_contour=PortalContour.TARGET,
            import_discovery_root=tmp_path / "incoming-restarted",
            import_staging_root=tmp_path / "staged-restarted",
            bundle_extract_root=tmp_path / "verified-restarted",
            operation_workspace_root=tmp_path / "operations-restarted",
        )
    )

    assert restarted.state.settings.operation_max_concurrent == 5
    with restarted.state.session_factory() as session:
        from app.services.transfer_settings import TransferSettingsService

        snapshot = TransferSettingsService(session, restarted.state.settings).resolve()
    assert snapshot.operation_max_concurrent == 5
    assert snapshot.operation_max_concurrent_active == 5
    assert snapshot.restart_required_fields == ()
