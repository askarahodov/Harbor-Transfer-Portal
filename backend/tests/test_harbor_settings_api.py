import json
import stat
from pathlib import Path

import certifi
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from alembic import command
from app.api.harbor import get_harbor_client
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import SettingMetadataRepository, UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app
from app.services.harbor_client import HarborClientError, HarborSystemInfo
from app.services.harbor_settings import HARBOR_URL_KEY, HarborSettingsError, HarborSettingsService

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


def test_harbor_url_rejects_embedded_credentials_and_validation_response_is_redacted(
    tmp_path: Path,
) -> None:
    client, _app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])
    secret = "must-not-be-reflected"

    response = client.patch(
        "/api/settings/harbor",
        json={"url": f"https://svc-transfer:{secret}@harbor.local"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert secret not in response.text
    safe = client.get("/api/settings/harbor", headers=headers)
    assert safe.json()["url"] == "https://bootstrap.harbor.local"


def test_harbor_url_policy_applies_to_bootstrap_and_persisted_override(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(harbor_url="https://bootstrap-user:bootstrap-secret@harbor.local")

    _client, app, _tokens = _app_client(tmp_path)
    with app.state.session_factory() as session:
        SettingMetadataRepository(session).set_value(
            HARBOR_URL_KEY,
            "https://db-user:db-secret@harbor.local",
        )
        session.commit()

        with pytest.raises(HarborSettingsError) as exc_info:
            HarborSettingsService(session, app.state.settings).resolve()

    assert exc_info.value.code == "harbor_configuration_invalid"
    assert "db-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://harbor.local/api/v2.0",
        "https://harbor.local?token=secret",
        "https://harbor.local#fragment",
    ],
)
def test_harbor_base_url_rejects_subpath_query_and_fragment(
    tmp_path: Path,
    unsafe_url: str,
) -> None:
    client, _app, tokens = _app_client(tmp_path)
    response = client.patch(
        "/api/settings/harbor",
        json={"url": unsafe_url},
        headers=_auth(tokens["admin"]),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


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
    harbor_events = [event for event in events if event.event_type.startswith("harbor.")]
    assert [event.event_type for event in harbor_events] == [
        "harbor.credential.rotated",
        "harbor.settings.updated",
    ]
    assert all(TEST_CREDENTIAL not in event.metadata_json for event in events)
    assert json.loads(harbor_events[0].metadata_json) == {"changed_fields": ["credential"]}
    assert json.loads(harbor_events[1].metadata_json) == {
        "changed_fields": ["url", "username"]
    }


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


def test_harbor_profile_create_rejects_blank_name_without_server_error(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)

    response = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "   ",
            "url": "https://prod.harbor.local",
            "username": "svc-prod",
            "verify_tls": True,
        },
        headers=_auth(tokens["admin"]),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_harbor_profiles_crud_is_safe_and_secrets_are_not_returned(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    admin = _auth(tokens["admin"])
    viewer = _auth(tokens["viewer"])

    initial = client.get("/api/settings/harbor/profiles", headers=viewer)
    assert initial.status_code == 200
    assert initial.json()["items"] == [
        {
            "id": "default",
            "name": "Default",
            "url": "https://bootstrap.harbor.local",
            "username": "bootstrap-user",
            "verify_tls": True,
            "enabled": True,
            "credential_configured": True,
            "custom_ca_configured": False,
            "legacy_default": True,
        }
    ]
    assert "bootstrap-" + "z" * 32 not in initial.text

    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "DR",
            "url": "https://dr.harbor.local",
            "username": "svc-dr",
            "verify_tls": True,
        },
        headers=admin,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert len(profile_id) == 32
    assert created.json()["credential_configured"] is False

    rotated = client.put(
        f"/api/settings/harbor/profiles/{profile_id}/credential",
        json={"secret": TEST_CREDENTIAL},
        headers=admin,
    )
    assert rotated.status_code == 200

    listed = client.get("/api/settings/harbor/profiles", headers=viewer)
    assert listed.status_code == 200
    dr = next(item for item in listed.json()["items"] if item["id"] == profile_id)
    assert dr["name"] == "DR"
    assert dr["credential_configured"] is True
    assert TEST_CREDENTIAL not in listed.text

    with app.state.session_factory() as session:
        service = HarborSettingsService(session, app.state.settings)
        resolved = service.resolve(profile_id)
        assert resolved.url == "https://dr.harbor.local"
        assert resolved.username == "svc-dr"
        assert resolved.password == TEST_CREDENTIAL

    denied = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "operator-cannot-create",
            "url": "https://operator.harbor.local",
        },
        headers=_auth(tokens["operator"]),
    )
    assert denied.status_code == 403

    deleted = client.delete(
        f"/api/settings/harbor/profiles/{profile_id}",
        headers=admin,
    )
    assert deleted.status_code == 200


def test_default_harbor_mutation_is_blocked_by_legacy_unpinned_operation(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    admin = _auth(tokens["admin"])

    with app.state.session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="admin",
            harbor_profile_id=None,
            harbor_profile_name=None,
            harbor_url=None,
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    blocked = client.patch(
        "/api/settings/harbor",
        json={"url": "https://changed.harbor.local"},
        headers=admin,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "harbor_profile_in_use"

    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.status = OperationStatus.COMPLETED
        session.commit()

    allowed = client.patch(
        "/api/settings/harbor",
        json={"url": "https://changed.harbor.local"},
        headers=admin,
    )
    assert allowed.status_code == 200
    assert allowed.json()["url"] == "https://changed.harbor.local"


def test_harbor_profile_mutation_is_blocked_while_operation_is_active(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    admin = _auth(tokens["admin"])
    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Production",
            "url": "https://prod.harbor.local",
            "username": "svc-prod",
            "verify_tls": True,
        },
        headers=admin,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]

    with app.state.session_factory() as session:
        operation = Operation(
            type=OperationType.EXPORT,
            status=OperationStatus.CREATED,
            actor_username="admin",
            harbor_profile_id=profile_id,
            harbor_profile_name="Production",
            harbor_url="https://prod.harbor.local",
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    blocked = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"url": "https://changed.harbor.local"},
        headers=admin,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "harbor_profile_in_use"

    blocked_credential = client.put(
        f"/api/settings/harbor/profiles/{profile_id}/credential",
        json={"secret": TEST_CREDENTIAL},
        headers=admin,
    )
    assert blocked_credential.status_code == 409

    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.status = OperationStatus.COMPLETED
        session.commit()

    allowed = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"url": "https://changed.harbor.local"},
        headers=admin,
    )
    assert allowed.status_code == 200
    assert allowed.json()["url"] == "https://changed.harbor.local"
