import json
import stat
from pathlib import Path

import pytest
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import BundleSource
from app.main import create_app
from app.services.bundle_package_service import (
    BundlePackageError,
    BundlePackageService,
    HelmChartPackageInput,
)
from app.services.key_management import KeyManagementService, public_key_fingerprint

JWT_TEST_KEY = "key-management-test-key-" + "x" * 32


def _private_pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _app_client(
    tmp_path: Path,
    contour: PortalContour,
) -> tuple[TestClient, object, dict[str, str]]:
    database_url = f"sqlite:///{tmp_path / f'{contour.value.lower()}-keys.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            portal_contour=contour,
            bundle_signing_private_key_file=tmp_path / "keys" / "source-signing-private.pem",
            bundle_trusted_public_keys_dir=tmp_path / "keys" / "trusted-source",
            operation_workspace_root=tmp_path / "operations",
            import_discovery_root=tmp_path / "incoming",
            import_staging_root=tmp_path / "staged",
            import_receipt_root=tmp_path / "receipts",
            bundle_extract_root=tmp_path / "verified",
            bundle_temp_root=tmp_path / "bundle-temp",
            bundle_payload_root=tmp_path / "payload",
            bundle_outgoing_root=tmp_path / "outgoing",
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


def test_source_signing_key_is_admin_only_rotatable_and_never_disclosed(tmp_path: Path) -> None:
    client, app, tokens = _app_client(tmp_path, PortalContour.SOURCE)
    admin_headers = _auth(tokens["admin"])

    initial = client.get("/api/settings/keys", headers=admin_headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "contour": "SOURCE",
        "signing": {"configured": False, "key_id": None, "fingerprint": None},
        "trusted_keys": [],
    }

    for role in ("operator", "viewer"):
        denied = client.get("/api/settings/keys", headers=_auth(tokens[role]))
        assert denied.status_code == 403

    first_key = Ed25519PrivateKey.generate()
    first_pem = _private_pem(first_key)
    installed = client.put(
        "/api/settings/keys/signing",
        json={"private_key_pem": first_pem},
        headers=admin_headers,
    )
    assert installed.status_code == 200
    expected_fingerprint = public_key_fingerprint(first_key.public_key())
    assert installed.json()["signing"]["fingerprint"] == expected_fingerprint
    assert first_pem not in installed.text

    key_path = app.state.settings.bundle_signing_private_key_file
    assert key_path.is_file()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    second_key = Ed25519PrivateKey.generate()
    second_pem = _private_pem(second_key)
    refused = client.put(
        "/api/settings/keys/signing",
        json={"private_key_pem": second_pem},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "signing_rotation_confirmation_required"
    assert second_pem not in refused.text
    assert public_key_fingerprint(first_key.public_key()) in client.get(
        "/api/settings/keys", headers=admin_headers
    ).text

    rotated = client.put(
        "/api/settings/keys/signing",
        json={"private_key_pem": second_pem, "confirm_rotation": True},
        headers=admin_headers,
    )
    assert rotated.status_code == 200
    assert rotated.json()["signing"]["fingerprint"] == public_key_fingerprint(
        second_key.public_key()
    )
    assert second_pem not in rotated.text

    with app.state.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.in_(("signing.key.installed", "signing.key.rotated")))
                .order_by(AuditEvent.id)
            )
        )
    assert [event.event_type for event in events] == [
        "signing.key.installed",
        "signing.key.rotated",
    ]
    serialized = "\n".join(event.metadata_json for event in events)
    assert first_pem not in serialized
    assert second_pem not in serialized
    assert json.loads(events[-1].metadata_json)["new_fingerprint"] == public_key_fingerprint(
        second_key.public_key()
    )


def test_target_trust_workflow_rejects_private_material_and_supports_rotation(
    tmp_path: Path,
) -> None:
    client, app, tokens = _app_client(tmp_path, PortalContour.TARGET)
    admin_headers = _auth(tokens["admin"])
    first_key = Ed25519PrivateKey.generate()
    second_key = Ed25519PrivateKey.generate()
    third_key = Ed25519PrivateKey.generate()

    private_as_public = _private_pem(first_key)
    rejected = client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": private_as_public, "confirm": True},
        headers=admin_headers,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "trusted_key_invalid"
    assert private_as_public not in rejected.text

    not_confirmed = client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": _public_pem(first_key)},
        headers=admin_headers,
    )
    assert not_confirmed.status_code == 409
    assert not_confirmed.json()["error"]["code"] == "trusted_key_confirmation_required"

    added_first = client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": _public_pem(first_key), "confirm": True},
        headers=admin_headers,
    )
    assert added_first.status_code == 200
    first_fingerprint = public_key_fingerprint(first_key.public_key())
    first_id = first_fingerprint.removeprefix("sha256:")
    assert added_first.json()["trusted_keys"] == [
        {"key_id": first_id, "fingerprint": first_fingerprint, "enabled": True}
    ]

    added_second = client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": _public_pem(second_key), "confirm": True},
        headers=admin_headers,
    )
    assert added_second.status_code == 200
    assert len(added_second.json()["trusted_keys"]) == 2

    disabled = client.patch(
        f"/api/settings/keys/trusted/{first_id}",
        json={"enabled": False, "confirm": True},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    first_state = next(
        item for item in disabled.json()["trusted_keys"] if item["key_id"] == first_id
    )
    assert first_state["enabled"] is False
    assert not (app.state.settings.bundle_trusted_public_keys_dir / f"{first_id}.pem").exists()

    enabled = client.patch(
        f"/api/settings/keys/trusted/{first_id}",
        json={"enabled": True, "confirm": True},
        headers=admin_headers,
    )
    assert enabled.status_code == 200
    assert next(
        item for item in enabled.json()["trusted_keys"] if item["key_id"] == first_id
    )["enabled"] is True

    second_id = public_key_fingerprint(second_key.public_key()).removeprefix("sha256:")
    replaced = client.put(
        f"/api/settings/keys/trusted/{second_id}",
        json={"public_key_pem": _public_pem(third_key), "confirm": True},
        headers=admin_headers,
    )
    assert replaced.status_code == 200
    third_id = public_key_fingerprint(third_key.public_key()).removeprefix("sha256:")
    assert {item["key_id"] for item in replaced.json()["trusted_keys"]} == {
        first_id,
        third_id,
    }

    readded_old = client.post(
        "/api/settings/keys/trusted",
        json={"public_key_pem": _public_pem(second_key), "confirm": True},
        headers=admin_headers,
    )
    assert readded_old.status_code == 200
    assert second_id in {item["key_id"] for item in readded_old.json()["trusted_keys"]}

    removal_without_confirmation = client.delete(
        f"/api/settings/keys/trusted/{first_id}",
        headers=admin_headers,
    )
    assert removal_without_confirmation.status_code == 409

    removed = client.delete(
        f"/api/settings/keys/trusted/{first_id}?confirm=true",
        headers=admin_headers,
    )
    assert removed.status_code == 200
    assert first_id not in {item["key_id"] for item in removed.json()["trusted_keys"]}

    for role in ("operator", "viewer"):
        denied = client.post(
            "/api/settings/keys/trusted",
            json={"public_key_pem": _public_pem(first_key), "confirm": True},
            headers=_auth(tokens[role]),
        )
        assert denied.status_code == 403

    with app.state.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.like("trust.key.%"))
                .order_by(AuditEvent.id)
            )
        )
    assert [event.event_type for event in events] == [
        "trust.key.added",
        "trust.key.added",
        "trust.key.disabled",
        "trust.key.enabled",
        "trust.key.replaced",
        "trust.key.added",
        "trust.key.removed",
    ]
    audit_payload = "\n".join(event.metadata_json for event in events)
    assert "BEGIN PUBLIC KEY" not in audit_payload
    assert "BEGIN PRIVATE KEY" not in audit_payload


def test_managed_target_trust_set_is_used_by_bundle_verifier(tmp_path: Path) -> None:
    signing_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()

    source_payload = tmp_path / "source-payload"
    chart = source_payload / "charts" / "demo-1.0.0.tgz"
    chart.parent.mkdir(parents=True)
    chart.write_bytes(b"deterministic-test-chart")

    source_settings = Settings(
        portal_contour=PortalContour.SOURCE,
        bundle_payload_root=source_payload,
        bundle_temp_root=tmp_path / "source-temp",
        bundle_outgoing_root=tmp_path / "source-outgoing",
        bundle_extract_root=tmp_path / "source-extract",
        bundle_signing_private_key_file=tmp_path / "source-keys" / "signing.pem",
        bundle_trusted_public_keys_dir=tmp_path / "unused-source-trust",
    )
    KeyManagementService(source_settings).install_signing_private_key(
        _private_pem(signing_key),
        confirm_rotation=False,
    )
    build = BundlePackageService(source_settings).build_bundle(
        source=BundleSource(
            contour="SOURCE",
            harbor="source.harbor.local",
            portal_version="0.1.0",
        ),
        created_by="admin",
        artifacts=(
            HelmChartPackageInput(
                repository="library",
                name="demo",
                version="1.0.0",
                source_digest=None,
                source_path=chart,
                payload_path="charts/demo-1.0.0.tgz",
            ),
        ),
    )

    target_settings = Settings(
        portal_contour=PortalContour.TARGET,
        bundle_payload_root=tmp_path / "target-payload",
        bundle_temp_root=tmp_path / "target-temp",
        bundle_outgoing_root=tmp_path / "target-outgoing",
        bundle_extract_root=tmp_path / "target-extract",
        bundle_signing_private_key_file=tmp_path / "unused-target-private.pem",
        bundle_trusted_public_keys_dir=tmp_path / "target-trust",
    )
    trust = KeyManagementService(target_settings)
    first = trust.add_trusted_key(_public_pem(signing_key))
    second = trust.add_trusted_key(_public_pem(other_key))

    verifier = BundlePackageService(target_settings)
    verified = verifier.verify_bundle(build.archive_path, sidecar_path=build.sidecar_path)
    assert verified.signing_key_fingerprint == first.fingerprint

    trust.set_trusted_key_enabled(first.key_id, enabled=False, confirm=True)
    with pytest.raises(BundlePackageError) as exc_info:
        verifier.verify_bundle(build.archive_path, sidecar_path=build.sidecar_path)
    assert exc_info.value.code == "bundle_signature_untrusted"

    trust.set_trusted_key_enabled(first.key_id, enabled=True, confirm=True)
    verified_again = verifier.verify_bundle(build.archive_path, sidecar_path=build.sidecar_path)
    assert verified_again.signing_key_fingerprint == first.fingerprint
    assert second.fingerprint != first.fingerprint
