import json
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_TEST_KEY = "transfer-policy-test-key-" + "x" * 32
MIB = 1024**2


def _build_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'transfer-policy.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            portal_contour=PortalContour.TARGET,
            operation_workspace_root=tmp_path / "operations",
            import_discovery_root=tmp_path / "incoming",
            import_staging_root=tmp_path / "staged",
            import_receipt_root=tmp_path / "receipts",
            bundle_extract_root=tmp_path / "verified",
            bundle_temp_root=tmp_path / "bundles",
            bundle_payload_root=tmp_path,
            bundle_trusted_public_keys_dir=tmp_path / "trusted",
        )
    )
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        if repo.get_by_username("admin") is None:
            for username, role in (
                ("admin", UserRole.ADMIN),
                ("operator", UserRole.OPERATOR),
                ("viewer", UserRole.VIEWER),
            ):
                repo.create(
                    username=username,
                    password_hash=hash_password(f"{username}-test-passphrase"),
                    role=role,
                )
            session.commit()
    return app


def _login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": f"{username}-test-passphrase"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_transfer_policy_is_admin_only_and_defaults_to_no_overwrite(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")
    operator = _login(client, "operator")
    viewer = _login(client, "viewer")

    response = client.get("/api/settings/transfer", headers=_auth(admin))
    assert response.status_code == 200
    assert response.json()["import_allow_overwrite"] is False
    assert response.json()["operation_max_concurrent"] == 2
    assert response.json()["effective_operation_max_concurrent"] == 2
    assert response.json()["restart_required_fields"] == []

    for token in (operator, viewer):
        assert client.get("/api/settings/transfer", headers=_auth(token)).status_code == 403
        denied = client.patch(
            "/api/settings/transfer",
            json={"import_allow_overwrite": True},
            headers=_auth(token),
        )
        assert denied.status_code == 403


def test_hot_transfer_policy_changes_are_effective_and_audited(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")
    operator = _login(client, "operator")

    payload = {
        "import_allow_overwrite": True,
        "import_max_upload_bytes": 2 * MIB,
        "bundle_max_archive_bytes": 4 * MIB,
        "bundle_max_extracted_bytes": 8 * MIB,
        "bundle_max_member_count": 100,
        "operation_disk_reserve_bytes": 64 * MIB,
    }
    response = client.patch(
        "/api/settings/transfer",
        json=payload,
        headers=_auth(admin),
    )
    assert response.status_code == 200
    for field, value in payload.items():
        assert response.json()[field] == value
        assert getattr(app.state.settings, field) == value

    too_large = client.post(
        "/api/imports/upload",
        content=b"x" * (2 * MIB + 1),
        headers=_auth(operator),
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "import_upload_too_large"

    with app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "transfer.policy.updated")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        metadata = json.loads(event.metadata_json)
    assert metadata["changed_fields"] == sorted(payload)
    assert metadata["before"]["import_allow_overwrite"] is False
    assert metadata["after"]["import_allow_overwrite"] is True
    assert metadata["restart_required_fields"] == []


def test_transfer_policy_rejects_inconsistent_limits_atomically(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")

    response = client.patch(
        "/api/settings/transfer",
        json={
            "import_max_upload_bytes": 8 * MIB,
            "bundle_max_archive_bytes": 4 * MIB,
        },
        headers=_auth(admin),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "transfer_policy_inconsistent"

    current = client.get("/api/settings/transfer", headers=_auth(admin)).json()
    assert current["import_max_upload_bytes"] == 50 * 1024**3
    assert current["bundle_max_archive_bytes"] == 50 * 1024**3


def test_concurrency_change_is_persisted_but_requires_restart(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")

    changed = client.patch(
        "/api/settings/transfer",
        json={"operation_max_concurrent": 5},
        headers=_auth(admin),
    )
    assert changed.status_code == 200
    assert changed.json()["operation_max_concurrent"] == 5
    assert changed.json()["effective_operation_max_concurrent"] == 2
    assert changed.json()["restart_required_fields"] == ["operation_max_concurrent"]
    assert app.state.settings.operation_max_concurrent == 2

    restarted_app = _build_app(tmp_path)
    with TestClient(restarted_app) as restarted:
        restarted_admin = _login(restarted, "admin")
        current = restarted.get(
            "/api/settings/transfer",
            headers=_auth(restarted_admin),
        )
        assert current.status_code == 200
        assert current.json()["operation_max_concurrent"] == 5
        assert current.json()["effective_operation_max_concurrent"] == 5
        assert current.json()["restart_required_fields"] == []
        assert restarted_app.state.settings.operation_max_concurrent == 5
