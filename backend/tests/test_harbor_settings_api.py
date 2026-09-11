import json
import stat
from pathlib import Path

import certifi
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.api.harbor import get_harbor_client
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.main import create_app
from app.services.harbor_client import HarborClientError, HarborSystemInfo
from app.services.harbor_settings import HarborSettingsService

JWT_TEST_KEY = "jwt-test-key-" + "x" * 32
TEST_CREDENTIAL = "credential-" + "y" * 32


class FakeHarborClient:
    def __init__(self, error: HarborClientError | None = None) -> None:
        self.error = error

    def system_info(self) -> HarborSystemInfo:
        if self.error is not None:
            raise self.error
        return HarborSystemInfo(harbor_version="2.13.0", auth_mode="db_auth")


def _app_client(tmp_path: Path) -> tuple[TestClient, object, dict[str, str]]:
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            harbor_url="https://bootstrap.harbor.local",
            harbor_user="bootstrap-user",
            harbor_password="bootstrap-" + "z" * 32,
            harbor_managed_secret_file=tmp_path / "secrets" / "harbor-password",
            harbor_managed_ca_file=tmp_path / "secrets" / "harbor-ca.pem",
        )
    )
    with app.state.session_factory() as session:
        repo = UserRepository(session)
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

    client = TestClient(app)
    tokens: dict[str, str] = {}
    for username in ("admin", "operator", "viewer"):
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": f"{username}-test-passphrase"},
        )
        assert response.status_code == 200
        tokens[username] = response.json()["access_token"]
    return client, app, tokens


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _one_ca_certificate() -> str:
    bundle = Path(certifi.where()).read_text(encoding="utf-8")
    first, _rest = bundle.split("-----END CERTIFICATE-----", 1)
    return first + "-----END CERTIFICATE-----\n"


def test_harbor_settings_are_admin_only_and_never_return_credential(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)

    admin = client.get("/api/settings/harbor", headers=_auth(tokens["admin"]))
    assert admin.status_code == 200
    assert admin.json()["url"] == "https://bootstrap.harbor.local"
    assert admin.json()["credential_configured"] is True
    assert "bootstrap-" + "z" * 32 not in admin.text

    for role in ("operator", "viewer"):
        denied = client.patch(
            "/api/settings/harbor",
            json={"url": "https://other.harbor.local"},
            headers=_auth(tokens[role]),
        )
        assert denied.status_code == 403


def test_nonsecret_patch_preserves_rotated_file_credential_and_audit_redacts_it(
    tmp_path: Path,
) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    rotated = client.put(
        "/api/settings/harbor/credential",
        json={"secret": TEST_CREDENTIAL},
        headers=headers,
    )
    assert rotated.status_code == 200
    credential_path = app.state.settings.harbor_managed_secret_file
    assert credential_path.read_text(encoding="utf-8") == TEST_CREDENTIAL
    assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600

    patched = client.patch(
        "/api/settings/harbor",
        json={"url": "https://new.harbor.local", "username": "svc-transfer"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["credential_configured"] is True
    assert credential_path.read_text(encoding="utf-8") == TEST_CREDENTIAL
    assert TEST_CREDENTIAL not in patched.text

    with app.state.session_factory() as session:
        events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    assert [event.event_type for event in events] == [
        "harbor.credential.rotated",
        "harbor.settings.updated",
    ]
    assert all(TEST_CREDENTIAL not in event.metadata_json for event in events)
    assert json.loads(events[0].metadata_json) == {"changed_fields": ["credential"]}
    assert json.loads(events[1].metadata_json) == {"changed_fields": ["url", "username"]}


def test_tls_disable_is_explicit_and_custom_ca_is_managed_file(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    disabled = client.patch(
        "/api/settings/harbor",
        json={"verify_tls": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["verify_tls"] is False

    enabled = client.patch(
        "/api/settings/harbor",
        json={"verify_tls": True},
        headers=headers,
    )
    assert enabled.status_code == 200

    uploaded = client.put(
        "/api/settings/harbor/ca",
        json={"certificate_pem": _one_ca_certificate()},
        headers=headers,
    )
    assert uploaded.status_code == 200
    ca_path = app.state.settings.harbor_managed_ca_file
    assert ca_path.is_file()
    assert stat.S_IMODE(ca_path.stat().st_mode) == 0o600

    safe = client.get("/api/settings/harbor", headers=headers)
    assert safe.json()["custom_ca_configured"] is True
    assert str(ca_path) not in safe.text

    with app.state.session_factory() as session:
        harbor_client = HarborSettingsService(session, app.state.settings).build_client()
        try:
            assert harbor_client.verify == str(ca_path)
        finally:
            harbor_client.close()


def test_connection_test_returns_sanitized_success_auth_and_tls_results(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    app.dependency_overrides[get_harbor_client] = lambda: FakeHarborClient()
    success = client.post("/api/settings/harbor/test", headers=headers)
    assert success.status_code == 200
    assert success.json()["code"] == "harbor_connection_ok"

    app.dependency_overrides[get_harbor_client] = lambda: FakeHarborClient(
        HarborClientError("unauthorized", "upstream detail", 401)
    )
    auth_failed = client.post("/api/settings/harbor/test", headers=headers)
    assert auth_failed.status_code == 200
    assert auth_failed.json()["code"] == "harbor_auth_failed"
    assert "upstream detail" not in auth_failed.text

    app.dependency_overrides[get_harbor_client] = lambda: FakeHarborClient(
        HarborClientError("tls_failed", "certificate detail")
    )
    tls_failed = client.post("/api/settings/harbor/test", headers=headers)
    assert tls_failed.status_code == 200
    assert tls_failed.json()["code"] == "harbor_tls_failed"
    assert "certificate detail" not in tls_failed.text
