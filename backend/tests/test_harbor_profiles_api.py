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
                "registry_type": "harbor",
                "nexus_repository_format": "legacy",
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



def test_profile_create_edit_disable_enable_and_delete_persist_registry_type(
    tmp_path: Path,
) -> None:
    """API must persist the type sent by the settings UI across every CRUD step."""
    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])
    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Legacy Nexus",
            "url": "http://nexus-legacy.example.test:8082",
            "username": "robot",
            "verify_tls": False,
            "enabled": True,
            "registry_type": "nexus",
        },
        headers=headers,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert created.json()["registry_type"] == "nexus"

    listed = client.get("/api/settings/harbor/profiles", headers=headers)
    assert listed.status_code == 200
    nexus_profile = next(
        item for item in listed.json()["items"] if item["id"] == profile_id
    )
    assert nexus_profile["registry_type"] == "nexus"
    assert nexus_profile["url"] == "http://nexus-legacy.example.test:8082"
    assert nexus_profile["is_active"] is False

    updated = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={
            "name": "Harbor DC-2",
            "url": "https://harbor-dc2.example.test",
            "username": "transfer-bot",
            "verify_tls": True,
            "registry_type": "harbor",
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Harbor DC-2"
    assert updated.json()["registry_type"] == "harbor"
    assert updated.json()["url"] == "https://harbor-dc2.example.test"
    assert updated.json()["username"] == "transfer-bot"
    assert updated.json()["verify_tls"] is True

    disabled = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["registry_type"] == "harbor"

    enabled = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"enabled": True},
        headers=headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    with app.state.session_factory() as session:
        profiles = HarborProfileService(session, app.state.settings)
        stored = profiles.get(profile_id)
        assert stored.name == "Harbor DC-2"
        assert stored.registry_type == "harbor"
        assert stored.url == "https://harbor-dc2.example.test"

    deleted = client.delete(
        f"/api/settings/harbor/profiles/{profile_id}",
        headers=headers,
    )
    assert deleted.status_code == 204
    listed_after = client.get("/api/settings/harbor/profiles", headers=headers)
    assert listed_after.status_code == 200
    assert [item["id"] for item in listed_after.json()["items"]] == ["default"]


def test_nexus_profile_connection_api_checks_v2_without_harbor_or_image(
    tmp_path: Path, monkeypatch,
) -> None:
    """Nexus settings action probes its connector, not Harbor REST or busybox."""
    from app.services.registry_probe import ProbeResult

    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])
    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Empty Nexus Docker hosted",
            "url": "https://docker.nexus.example.test",
            "username": "operator",
            "verify_tls": True,
            "enabled": True,
            "registry_type": "nexus",
        },
        headers=headers,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    calls = []

    async def fake_docker_probe(resolved, *, connect_timeout, read_timeout):
        calls.append({
            "url": resolved.url,
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
        })
        return ProbeResult(
            ok=True, code="registry_connection_ok",
            message="Docker Registry v2 доступен",
        )

    def forbidden_harbor_client(*_args, **_kwargs):
        raise AssertionError("Nexus settings action must not invoke Harbor API")

    monkeypatch.setattr("app.api.settings.probe_docker_v2", fake_docker_probe)
    monkeypatch.setattr(
        "app.services.harbor_profiles.HarborProfileService.build_client",
        forbidden_harbor_client,
    )
    response = client.post(
        f"/api/settings/harbor/profiles/{profile_id}/test-registry",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "code": "registry_connection_ok",
        "message": "Docker Registry v2 доступен", "version": None,
    }
    assert len(calls) == 1
    assert calls[0]["url"] == "https://docker.nexus.example.test"
    assert "busybox" not in response.text
    assert "Nexus (version unknown)" not in response.text


def test_nexus_helm_http_profile_pins_repository_and_preserves_tls_setting(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.services.nexus_helm_http import NexusHelmHttpError, NexusHelmHttpService
    from app.services.nexus_service import NexusService, NexusServiceError
    from app.services.registry_probe import ProbeResult

    client, app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])

    missing = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Incomplete Helm",
            "url": "https://nexus-ui.example.test",
            "registry_type": "nexus",
            "nexus_repository_format": "helm_http",
            "verify_tls": False,
        },
        headers=headers,
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "nexus_helm_profile_incomplete"

    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Regional Helm hosted",
            "url": "https://nexus-ui.example.test",
            "registry_type": "nexus",
            "nexus_repository_format": "helm_http",
            "nexus_helm_repository": "helm-hosted",
            "nexus_search_url": "https://nexus-ui.example.test",
            "verify_tls": False,
            "username": "helm-operator",
        },
        headers=headers,
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert created.json()["nexus_repository_format"] == "helm_http"
    assert created.json()["nexus_helm_repository"] == "helm-hosted"
    assert created.json()["verify_tls"] is False

    observed = []

    async def fake_helm_probe(resolved, repository, *, connect_timeout, read_timeout):
        observed.append((resolved.url, repository, resolved.verify_tls))
        return ProbeResult(ok=True, code="helm_repository_ok", message="Helm HTTP доступен")

    async def forbidden_docker(*_args, **_kwargs):
        raise AssertionError("Docker v2 probe is forbidden for Helm profile")

    monkeypatch.setattr("app.api.settings.probe_helm_http", fake_helm_probe)
    monkeypatch.setattr("app.api.settings.probe_docker_v2", forbidden_docker)
    result = client.post(
        f"/api/settings/harbor/profiles/{profile_id}/test-registry", headers=headers,
    )
    assert result.status_code == 200
    assert result.json()["code"] == "helm_repository_ok"
    assert observed == [("https://nexus-ui.example.test", "helm-hosted", False)]

    with app.state.session_factory() as session:
        helm = NexusHelmHttpService(session, app.state.settings, profile_id)
        assert helm.base_url == "https://nexus-ui.example.test"
        assert helm._repository("helm-hosted") == "helm-hosted"
        try:
            helm._repository("other-repository")
        except NexusHelmHttpError as exc:
            assert exc.code == "helm_repository_mismatch"
        else:
            raise AssertionError("profile must reject access to unbound Helm repository")
        try:
            NexusService(session, app.state.settings, nexus_profile_id=profile_id)._resolve_nexus()
        except NexusServiceError as exc:
            assert exc.code == "nexus_profile_invalid_format"
        else:
            raise AssertionError("Docker operations must reject a Helm HTTP profile")

    mismatched = client.patch(
        f"/api/settings/harbor/profiles/{profile_id}",
        json={"nexus_search_url": "https://foreign.example.test"},
        headers=headers,
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "nexus_helm_profile_incomplete"

    listed = client.get("/api/settings/harbor/profiles", headers=headers)
    persisted = next(item for item in listed.json()["items"] if item["id"] == profile_id)
    assert persisted["nexus_helm_repository"] == "helm-hosted"
    assert persisted["nexus_repository_format"] == "helm_http"


def test_legacy_nexus_profile_does_not_change_format_when_loaded(tmp_path: Path) -> None:
    client, _app, tokens = _app_client(tmp_path)
    headers = _auth(tokens["admin"])
    created = client.post(
        "/api/settings/harbor/profiles",
        json={
            "name": "Existing Nexus",
            "url": "https://nexus.example.test",
            "registry_type": "nexus",
            "verify_tls": False,
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["nexus_repository_format"] == "legacy"
    listed = client.get("/api/settings/harbor/profiles", headers=headers)
    assert next(
        item for item in listed.json()["items"] if item["id"] == created.json()["id"]
    )["nexus_repository_format"] == "legacy"
