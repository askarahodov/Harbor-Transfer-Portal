import json
import stat
from pathlib import Path

import certifi
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app
from app.services.harbor_profiles import DEFAULT_PROFILE_ID, HarborProfileService

JWT_TEST_KEY = "jwt-test-key-" + "x" * 32
PROFILE_SECRET = "profile-secret-" + "s" * 32


def _app_client(tmp_path: Path) -> tuple[TestClient, object, dict[str, str]]:
    database_url = f"sqlite:///{tmp_path / 'profiles.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            harbor_url="https://legacy.harbor.local",
            harbor_user="legacy-user",
            harbor_password="legacy-" + "z" * 32,
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


def test_existing_single_harbor_is_exposed_as_default_profile(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)

    response = client.get(
        "/api/settings/harbor/profiles",
        headers=_auth(tokens["admin"]),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "default",
                "name": "Default Harbor",
                "url": "https://legacy.harbor.local",
                "username": "legacy-user",
                "verify_tls": True,
                "enabled": True,
                "credential_configured": True,
                "custom_ca_configured": False,
                "is_default": True,
                "is_active": True,
            }
        ]
    }


def test_profile_management_is_admin_only_and_redacts_credentials(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)
    payload = {
        "name": "Harbor DC-2",
        "url": "https://harbor-dc2.local",
        "username": "svc-transfer",
        "verify_tls": True,
        "enabled": True,
    }

    for role in ("operator", "viewer"):
        denied = client.post(
            "/api/settings/harbor/profiles",
            json=payload,
            headers=_auth(tokens[role]),
        )
        assert denied.status_code == 403

    created = client.post(
        "/api/settings/harbor/profiles",
        json=payload,
        headers=_auth(tokens["admin"]),
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert created.json()["name"] == "Harbor DC-2"
    assert created.json()["credential_configured"] is False

    rotated = client.put(
        f"/api/settings/harbor/profiles/{profile_id}/credential",
        json={"secret": PROFILE_SECRET},
        headers=_auth(tokens["admin"]),
    )
    assert rotated.status_code == 200
    assert PROFILE_SECRET not in rotated.text

    listed = client.get(
        "/api/settings/harbor/profiles",
        headers=_auth(tokens["admin"]),
    )
    assert listed.status_code == 200
    profile = next(item for item in listed.json()["items"] if item["id"] == profile_id)
    assert profile["credential_configured"] is True
    assert PROFILE_SECRET not in listed.text


def test_profile_activation_changes_runtime_harbor_and_is_audited(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Harbor DC-2",
            "url": "https://harbor-dc2.local",
            "username": "svc-transfer",
            "verify_tls": True,
            "enabled": True,
        },
        headers=headers,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert created.json()["is_active"] is False

    secret = "active-profile-" + "q" * 32
    assert client.put(
        f"/api/settings/harbor/profiles/{profile_id}/credential",
        json={"secret": secret},
        headers=headers,
    ).status_code == 200

    activated = client.put(
        f"/api/settings/harbor/profiles/{profile_id}/activate",
        headers=headers,
    )
    assert activated.status_code == 200
    assert activated.json()["id"] == profile_id
    assert activated.json()["is_active"] is True

    listed = client.get("/api/settings/harbor/profiles", headers=headers)
    assert listed.status_code == 200
    active = [item for item in listed.json()["items"] if item["is_active"]]
    assert [item["id"] for item in active] == [profile_id]
    default = next(item for item in listed.json()["items"] if item["id"] == "default")
    assert default["url"] == "https://legacy.harbor.local"
    assert default["username"] == "legacy-user"
    assert default["is_active"] is False

    default_settings = client.get("/api/settings/harbor", headers=headers)
    assert default_settings.status_code == 200
    assert default_settings.json()["url"] == "https://legacy.harbor.local"
    assert default_settings.json()["username"] == "legacy-user"

    with app.state.session_factory() as session:
        service = HarborProfileService(session, app.state.settings)
        resolved = service.legacy.resolve()
        assert resolved.url == "https://harbor-dc2.local"
        assert resolved.username == "svc-transfer"
        assert resolved.password == secret

        default_client = service.build_client(DEFAULT_PROFILE_ID)
        try:
            assert default_client.base_url == "https://legacy.harbor.local"
        finally:
            default_client.close()

        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == "harbor.profile.activated")
                .order_by(AuditEvent.id)
            )
        )
    assert len(events) == 1
    metadata = json.loads(events[0].metadata_json)
    assert metadata["previous_profile_id"] == "default"
    assert metadata["active_profile_id"] == profile_id
    assert secret not in events[0].metadata_json


def test_active_profile_cannot_be_disabled_deleted_or_switched_while_busy(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Active Harbor",
            "url": "https://active.harbor.local",
            "verify_tls": True,
            "enabled": True,
        },
        headers=headers,
    )
    profile_id = created.json()["id"]
    assert client.put(
        f"/api/settings/harbor/profiles/{profile_id}/activate",
        headers=headers,
    ).status_code == 200

    disabled = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 409
    assert disabled.json()["error"]["code"] == "harbor_profile_active_protected"

    deleted = client.delete(
        f"/api/settings/harbor/profiles/{profile_id}",
        headers=headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "harbor_profile_active_protected"

    with app.state.session_factory() as session:
        session.add(
            Operation(
                type=OperationType.EXPORT,
                status=OperationStatus.RUNNING,
                actor_username="admin",
                harbor_profile_id=profile_id,
                harbor_profile_name="Active Harbor",
                harbor_url="https://active.harbor.local",
            )
        )
        session.commit()

    busy = client.put("/api/settings/harbor/profiles/default/activate", headers=headers)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "harbor_profile_busy"

    active_patch = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"url": "https://changed-active.harbor.local"},
        headers=headers,
    )
    assert active_patch.status_code == 409
    assert active_patch.json()["error"]["code"] == "harbor_profile_busy"

    active_credential = client.put(
        f"/api/settings/harbor/profiles/{profile_id}/credential",
        json={"secret": "blocked-" + "x" * 32},
        headers=headers,
    )
    assert active_credential.status_code == 409
    assert active_credential.json()["error"]["code"] == "harbor_profile_busy"

    inactive_default_patch = client.patch(
        "/api/settings/harbor",
        json={"url": "https://prepared-default.harbor.local"},
        headers=headers,
    )
    assert inactive_default_patch.status_code == 200
    assert inactive_default_patch.json()["url"] == "https://prepared-default.harbor.local"


def test_authenticated_profile_options_are_safe_and_enabled_only(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    enabled = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Transfer A",
            "url": "https://transfer-a.local",
            "username": "svc-a",
            "verify_tls": True,
            "enabled": True,
        },
        headers=headers,
    )
    disabled = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Disabled B",
            "url": "https://disabled-b.local",
            "username": "svc-b",
            "verify_tls": True,
            "enabled": False,
        },
        headers=headers,
    )
    assert enabled.status_code == 201
    assert disabled.status_code == 201

    for role in ("admin", "operator", "viewer"):
        response = client.get("/api/harbor/profiles", headers=_auth(tokens[role]))
        assert response.status_code == 200
        items = response.json()["items"]
        assert {item["name"] for item in items} == {"Default Harbor", "Transfer A"}
        assert all(set(item) == {"id", "name", "url", "is_default"} for item in items)
        assert "credential" not in response.text.lower()
        assert "username" not in response.text.lower()


def test_profile_delete_is_blocked_when_history_references_snapshot(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])
    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Historical Harbor",
            "url": "https://history.harbor.local",
            "verify_tls": True,
            "enabled": True,
        },
        headers=headers,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]

    with app.state.session_factory() as session:
        session.add(
            Operation(
                type=OperationType.EXPORT,
                status=OperationStatus.COMPLETED,
                actor_username="admin",
                harbor_profile_id=profile_id,
                harbor_profile_name="Historical Harbor",
                harbor_url="https://history.harbor.local",
            )
        )
        session.commit()

    deleted = client.delete(
        f"/api/settings/harbor/profiles/{profile_id}",
        headers=headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "harbor_profile_referenced"


def test_profile_credentials_and_ca_are_isolated_by_profile(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    profile_ids: list[str] = []
    for name, host in (("Harbor A", "a.harbor.local"), ("Harbor B", "b.harbor.local")):
        created = client.post(
            "/api/settings/harbor/profiles",
            json={
                "name": name,
                "url": f"https://{host}",
                "username": "svc",
                "verify_tls": True,
                "enabled": True,
            },
            headers=headers,
        )
        assert created.status_code == 201
        profile_ids.append(created.json()["id"])

    first, second = profile_ids
    first_secret = "first-" + "a" * 40
    second_secret = "second-" + "b" * 40
    assert client.put(
        f"/api/settings/harbor/profiles/{first}/credential",
        json={"secret": first_secret},
        headers=headers,
    ).status_code == 200
    assert client.put(
        f"/api/settings/harbor/profiles/{second}/credential",
        json={"secret": second_secret},
        headers=headers,
    ).status_code == 200
    assert client.put(
        f"/api/settings/harbor/profiles/{first}/ca",
        json={"certificate_pem": _one_ca_certificate()},
        headers=headers,
    ).status_code == 200

    with app.state.session_factory() as session:
        service = HarborProfileService(session, app.state.settings)
        first_profile = service.get(first)
        second_profile = service.get(second)
        first_secret_path = service._credential_path(first_profile)
        second_secret_path = service._credential_path(second_profile)
        first_ca_path = service._ca_path(first_profile)
        second_ca_path = service._ca_path(second_profile)

        assert first_secret_path.read_text(encoding="utf-8") == first_secret
        assert second_secret_path.read_text(encoding="utf-8") == second_secret
        assert first_secret_path != second_secret_path
        assert stat.S_IMODE(first_secret_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(second_secret_path.stat().st_mode) == 0o600
        assert first_ca_path.is_file()
        assert not second_ca_path.exists()

        first_client = service.build_client(first)
        second_client = service.build_client(second)
        try:
            assert first_client.base_url == "https://a.harbor.local"
            assert second_client.base_url == "https://b.harbor.local"
            assert first_client.verify == str(first_ca_path)
            assert second_client.verify is True
        finally:
            first_client.close()
            second_client.close()


def test_default_profile_cannot_be_deleted_or_patched_through_profile_api(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    deleted = client.delete(
        f"/api/settings/harbor/profiles/{DEFAULT_PROFILE_ID}",
        headers=headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "harbor_profile_default_protected"

    patched = client.patch(
        f"/api/settings/harbor/profiles/{DEFAULT_PROFILE_ID}",
        json={"name": "Renamed"},
        headers=headers,
    )
    assert patched.status_code == 409
    assert patched.json()["error"]["code"] == "harbor_profile_default_managed_elsewhere"


def test_profile_name_uniqueness_and_safe_audit_metadata(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    first = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Primary Lab",
            "url": "https://lab-a.local",
            "username": "svc-a",
            "verify_tls": True,
            "enabled": True,
        },
        headers=headers,
    )
    assert first.status_code == 201
    profile_id = first.json()["id"]

    duplicate = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": " primary   lab ",
            "url": "https://lab-b.local",
            "verify_tls": True,
            "enabled": True,
        },
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "harbor_profile_name_conflict"

    secret = "audit-secret-" + "x" * 40
    assert client.put(
        f"/api/settings/harbor/profiles/{profile_id}/credential",
        json={"secret": secret},
        headers=headers,
    ).status_code == 200

    with app.state.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.like("harbor.profile.%"))
                .order_by(AuditEvent.id)
            )
        )

    assert [event.event_type for event in events] == [
        "harbor.profile.created",
        "harbor.profile.credential.rotated",
    ]
    assert all(secret not in event.metadata_json for event in events)
    metadata = json.loads(events[-1].metadata_json)
    assert metadata["profile_id"] == profile_id
    assert metadata["profile_name"] == "Primary Lab"
    assert metadata["changed_fields"] == ["credential"]
