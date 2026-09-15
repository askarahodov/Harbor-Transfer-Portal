import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_TEST_KEY = "destination-mapping-policy-test-key-" + "x" * 32


def _build_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'destination-mapping-policy.db'}"
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


def test_destination_mapping_defaults_are_admin_only_and_fail_closed_by_default(
    tmp_path: Path,
) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")
    operator = _login(client, "operator")
    viewer = _login(client, "viewer")

    current = client.get("/api/settings/transfer", headers=_auth(admin))
    assert current.status_code == 200
    assert current.json()["destination_mapping_revision"] == 0
    assert current.json()["destination_container_image_project"] is None
    assert current.json()["destination_helm_chart_project"] is None
    assert current.json()["destination_project_mappings"] == {}

    for token in (operator, viewer):
        denied = client.patch(
            "/api/settings/transfer",
            json={"destination_container_image_project": "images-default"},
            headers=_auth(token),
        )
        assert denied.status_code == 403


def test_destination_mapping_defaults_persist_revision_and_safe_audit(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")
    payload = {
        "destination_container_image_project": "images-default",
        "destination_helm_chart_project": "helm-default",
        "destination_project_mappings": {
            "source-a": "target-a",
            "source-b": "target-b",
        },
    }

    changed = client.patch(
        "/api/settings/transfer",
        json=payload,
        headers=_auth(admin),
    )
    assert changed.status_code == 200
    body = changed.json()
    assert body["destination_mapping_revision"] == 1
    for field, value in payload.items():
        assert body[field] == value

    no_op = client.patch(
        "/api/settings/transfer",
        json={"destination_container_image_project": "images-default"},
        headers=_auth(admin),
    )
    assert no_op.status_code == 200
    assert no_op.json()["destination_mapping_revision"] == 1

    restarted = _build_app(tmp_path)
    restarted_client = TestClient(restarted)
    restarted_admin = _login(restarted_client, "admin")
    persisted = restarted_client.get(
        "/api/settings/transfer",
        headers=_auth(restarted_admin),
    )
    assert persisted.status_code == 200
    assert persisted.json()["destination_mapping_revision"] == 1
    assert persisted.json()["destination_project_mappings"] == payload[
        "destination_project_mappings"
    ]

    with app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "transfer.policy.updated")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        metadata = json.loads(event.metadata_json)
    assert metadata["after"]["destination_mapping_revision"] == 1
    assert metadata["before"]["destination_mapping_revision"] == 0
    assert metadata["after"]["destination_container_image_project"] == "images-default"
    assert "credential" not in event.metadata_json.lower()
    assert "secret" not in event.metadata_json.lower()


def test_destination_mapping_settings_validate_and_can_be_cleared(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    admin = _login(client, "admin")

    invalid = client.patch(
        "/api/settings/transfer",
        json={"destination_container_image_project": "Team/Images"},
        headers=_auth(admin),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"

    created = client.patch(
        "/api/settings/transfer",
        json={
            "destination_container_image_project": "images-default",
            "destination_project_mappings": {"source": "target"},
        },
        headers=_auth(admin),
    )
    assert created.status_code == 200
    assert created.json()["destination_mapping_revision"] == 1

    cleared = client.patch(
        "/api/settings/transfer",
        json={
            "destination_container_image_project": None,
            "destination_project_mappings": {},
        },
        headers=_auth(admin),
    )
    assert cleared.status_code == 200
    assert cleared.json()["destination_mapping_revision"] == 2
    assert cleared.json()["destination_container_image_project"] is None
    assert cleared.json()["destination_project_mappings"] == {}
