import json
from pathlib import Path

import pytest
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
from app.services.harbor_settings import resolve_harbor_settings

JWT_SECRET = "test-jwt-secret-not-for-production-123456"


class FakeHarborClient:
    def __init__(self, error: HarborClientError | None = None) -> None:
        self.error = error

    def system_info(self) -> HarborSystemInfo:
        if self.error is not None:
            raise self.error
        return HarborSystemInfo(harbor_version="2.13.0", auth_mode="db_auth")


def _app(tmp_path: Path) -> tuple[TestClient, object]:
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            harbor_url="https://bootstrap-harbor.local",
            harbor_user="bootstrap-user",
            harbor_password="bootstrap-secret",
            harbor_runtime_password_file=tmp_path / "secrets" / "harbor-password",
            harbor_runtime_ca_file=tmp_path / "secrets" / "harbor-ca.crt",
        )
    )
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        repo.create(
            username="admin",
            password_hash=hash_password("admin-password-123"),
            role=UserRole.ADMIN,
        )
        repo.create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
    return TestClient(app), app


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_safe_settings_are_admin_only_and_redact_secret(tmp_path: Path) -> None:
    client, _app_instance = _app(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    operator = _login(client, "operator", "operator-password-123")

    forbidden = client.get("/api/settings/harbor", headers=_auth(operator))
    assert forbidden.status_code == 403

    response = client.get("/api/settings/harbor", headers=_auth(admin))
    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_configured"] is True
    assert payload["url"].startswith("https://bootstrap-harbor.local")
    assert "bootstrap-secret" not in response.text
    assert "harbor-password" not in response.text


def test_url_update_preserves_rotated_credential_and_audit_redacts_it(tmp_path: Path) -> None:
    client, app = _app(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    secret = "runtime-secret-that-must-never-be-returned"

    rotated = client.put(
        "/api/settings/harbor/credential",
        json={"secret": secret},
        headers=_auth(admin),
    )
    assert rotated.status_code == 200
    secret_file = app.state.settings.harbor_runtime_password_file
    assert secret_file.read_text(encoding="utf-8") == secret
    assert oct(secret_file.stat().st_mode & 0o777) == "0o600"

    updated = client.patch(
        "/api/settings/harbor",
        json={"url": "https://new-harbor.local", "verify_tls": False},
        headers=_auth(admin),
    )
    assert updated.status_code == 200
    assert updated.json()["credential_configured"] is True
    assert secret_file.read_text(encoding="utf-8") == secret
    assert secret not in updated.text

    with app.state.session_factory() as session:
        effective = resolve_harbor_settings(session, app.state.settings)
        assert str(effective.harbor_url).startswith("https://new-harbor.local")
        assert effective.harbor_password is not None
        assert effective.harbor_password.get_secret_value() == secret
        events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))

    assert [event.event_type for event in events] == [
        "harbor.credential.rotated",
        "harbor.settings.updated",
    ]
    serialized = " ".join(event.metadata_json for event in events)
    assert secret not in serialized
    assert "harbor_credential" in serialized
    assert set(json.loads(events[-1].metadata_json)["changed_fields"]) == {
        "harbor_url",
        "harbor_verify_tls",
    }


def test_runtime_ca_upload_and_clear_use_server_owned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app = _app(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    monkeypatch.setattr(
        "app.services.harbor_settings.validate_ca_pem",
        lambda _certificate_pem: None,
    )

    response = client.put(
        "/api/settings/harbor/ca",
        json={"certificate_pem": "test-ca-pem"},
        headers=_auth(admin),
    )
    assert response.status_code == 200
    assert response.json()["custom_ca_configured"] is True
    assert response.json()["custom_ca_source"] == "runtime"
    ca_file = app.state.settings.harbor_runtime_ca_file
    assert ca_file.read_text(encoding="utf-8") == "test-ca-pem"
    assert oct(ca_file.stat().st_mode & 0o777) == "0o600"

    cleared = client.delete("/api/settings/harbor/ca", headers=_auth(admin))
    assert cleared.status_code == 200
    assert cleared.json()["custom_ca_configured"] is False
    assert not ca_file.exists()


def test_invalid_ca_is_rejected_without_writing_file(tmp_path: Path) -> None:
    client, app = _app(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    response = client.put(
        "/api/settings/harbor/ca",
        json={"certificate_pem": "not a certificate"},
        headers=_auth(admin),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "harbor_ca_invalid"
    assert not app.state.settings.harbor_runtime_ca_file.exists()


def test_connection_check_is_sanitized(tmp_path: Path) -> None:
    client, app = _app(tmp_path)
    admin = _login(client, "admin", "admin-password-123")

    app.dependency_overrides[get_harbor_client] = lambda: FakeHarborClient()
    connected = client.post("/api/settings/harbor/test", headers=_auth(admin))
    assert connected.status_code == 200
    assert connected.json() == {
        "connected": True,
        "version": "2.13.0",
        "auth_mode": "db_auth",
    }

    app.dependency_overrides[get_harbor_client] = lambda: FakeHarborClient(
        HarborClientError("unauthorized", "raw-upstream-secret", 401)
    )
    rejected = client.post("/api/settings/harbor/test", headers=_auth(admin))
    assert rejected.status_code == 502
    assert rejected.json()["error"]["code"] == "harbor_auth_failed"
    assert "raw-upstream-secret" not in rejected.text
