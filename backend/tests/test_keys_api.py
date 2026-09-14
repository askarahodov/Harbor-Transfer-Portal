import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_TEST_KEY = "key-management-test-key-" + "x" * 32


def _private_pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _build_app(tmp_path: Path, contour: PortalContour):
    database_url = f"sqlite:///{tmp_path / f'keys-{contour.value.lower()}.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            portal_contour=contour,
            operation_workspace_root=tmp_path / "operations",
            import_discovery_root=tmp_path / "incoming",
            import_staging_root=tmp_path / "staged",
            import_receipt_root=tmp_path / "receipts",
            bundle_payload_root=tmp_path,
            bundle_extract_root=tmp_path / "verified",
            bundle_temp_root=tmp_path / "bundle-tmp",
            bundle_outgoing_root=tmp_path / "outgoing",
            bundle_signing_private_key_file=tmp_path / "keys" / "source-private.pem",
            bundle_trusted_public_keys_dir=tmp_path / "keys" / "trusted",
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


def test_source_key_api_is_admin_only_and_never_returns_private_key(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.SOURCE)
    client = TestClient(app)
    admin = _login(client, "admin")
    operator = _login(client, "operator")
    viewer = _login(client, "viewer")
    pem = _private_pem(Ed25519PrivateKey.generate())

    for token in (operator, viewer):
        assert client.get("/api/settings/keys", headers=_auth(token)).status_code == 403
        denied = client.put(
            "/api/settings/keys/source-signing",
            json={"private_key_pem": pem, "confirm": True},
            headers=_auth(token),
        )
        assert denied.status_code == 403

    installed = client.put(
        "/api/settings/keys/source-signing",
        json={"private_key_pem": pem, "confirm": True},
        headers=_auth(admin),
    )
    assert installed.status_code == 200
    payload = installed.json()
    assert payload["configured"] is True
    assert payload["fingerprint"].startswith("sha256:")
    assert "private" not in json.dumps(payload).lower()
    assert pem not in installed.text

    status_response = client.get("/api/settings/keys", headers=_auth(admin))
    assert status_response.status_code == 200
    assert status_response.json()["source_signing"] == payload
    assert "private_key_pem" not in status_response.text

    with app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "keys.source_signing.installed")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        metadata = json.loads(event.metadata_json)
    assert metadata == {"fingerprint": payload["fingerprint"]}
    assert pem not in event.metadata_json


def test_target_trust_api_rejects_private_material_and_manages_overlap(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.TARGET)
    client = TestClient(app)
    admin = _login(client, "admin")
    first = Ed25519PrivateKey.generate()
    second = Ed25519PrivateKey.generate()

    rejected = client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": _private_pem(first), "confirm": True},
        headers=_auth(admin),
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "key_public_invalid"

    records = []
    for key in (first, second):
        response = client.post(
            "/api/settings/keys/trusted",
            json={"public_key_pem": _public_pem(key), "confirm": True},
            headers=_auth(admin),
        )
        assert response.status_code == 200
        records.append(response.json())

    current = client.get("/api/settings/keys", headers=_auth(admin)).json()
    assert current["source_signing"] is None
    assert len(current["trusted_keys"]) == 2
    assert all(item["enabled"] for item in current["trusted_keys"])

    fingerprint = records[0]["fingerprint"]
    disabled = client.post(
        f"/api/settings/keys/trusted/{fingerprint}/disable",
        json={"confirm": True},
        headers=_auth(admin),
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    enabled = client.post(
        f"/api/settings/keys/trusted/{fingerprint}/enable",
        json={"confirm": True},
        headers=_auth(admin),
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    removed = client.post(
        f"/api/settings/keys/trusted/{fingerprint}/remove",
        json={"confirm": True},
        headers=_auth(admin),
    )
    assert removed.status_code == 204

    with app.state.session_factory() as session:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.event_type.like("keys.trusted.%"))
        ).all()
    assert {event.event_type for event in events} >= {
        "keys.trusted.added",
        "keys.trusted.disabled",
        "keys.trusted.enabled",
        "keys.trusted.removed",
    }
    combined = "\n".join(event.metadata_json for event in events)
    assert "BEGIN PUBLIC KEY" not in combined
    assert "BEGIN PRIVATE KEY" not in combined


def test_key_api_enforces_contour_and_explicit_confirmation(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path / "source", PortalContour.SOURCE)
    source_client = TestClient(source_app)
    source_admin = _login(source_client, "admin")
    public_pem = _public_pem(Ed25519PrivateKey.generate())

    wrong = source_client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": public_pem, "confirm": True},
        headers=_auth(source_admin),
    )
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "key_wrong_contour"

    unconfirmed = source_client.put(
        "/api/settings/keys/source-signing",
        json={
            "private_key_pem": _private_pem(Ed25519PrivateKey.generate()),
            "confirm": False,
        },
        headers=_auth(source_admin),
    )
    assert unconfirmed.status_code == 422
