from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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
from app.services.bundle_package_service import BundlePackageService, HelmChartPackageInput
from app.services.key_material import ed25519_public_key_fingerprint

JWT_SECRET = "universal-mode-key-isolation-test-" + "x" * 32


def _private_pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def _app(tmp_path: Path):
    root = tmp_path / "universal"
    root.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{root / 'portal.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
        portal_contour=PortalContour.SOURCE,
        bundle_payload_root=root / "data",
        bundle_temp_root=root / "data" / "tmp" / "bundles",
        bundle_outgoing_root=root / "data" / "outgoing",
        bundle_extract_root=root / "data" / "verified",
        bundle_signing_private_key_file=root / "keys" / "source-signing-private.pem",
        bundle_trusted_public_keys_dir=root / "keys" / "trusted-source",
        operation_workspace_root=root / "data" / "tmp" / "operations",
        import_discovery_root=root / "data" / "incoming",
        import_staging_root=root / "data" / "staged",
        import_receipt_root=root / "data" / "receipts",
    )
    app = create_app(settings)
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        repo.create(
            username="admin",
            password_hash=hash_password("admin-test-passphrase"),
            role=UserRole.ADMIN,
        )
        session.commit()
    return app


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-test-passphrase"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _switch(client: TestClient, headers: dict[str, str], mode: PortalContour) -> None:
    response = client.put(
        "/api/runtime/mode",
        json={"mode": mode.value},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["current"] == mode.value


def _build_bundle(settings: Settings, delivery_id: str):
    payload = settings.bundle_payload_root / "packages" / f"{delivery_id}.tgz"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"universal-mode-key-isolation-chart")
    return BundlePackageService(settings).build_bundle(
        source=BundleSource(
            contour="SOURCE",
            harbor="harbor.source.local",
            portal_version="1.0.0",
        ),
        created_by="admin",
        artifacts=[
            HelmChartPackageInput(
                repository="project/charts",
                name="sample",
                version="1.2.3",
                source_digest=None,
                source_path=payload,
                payload_path="charts/sample.tgz",
            )
        ],
        delivery_id=delivery_id,
        created_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )


def _directory_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_runtime_switch_preserves_signing_and_trust_material_byte_for_byte(tmp_path: Path) -> None:
    app = _app(tmp_path)
    signing_key = Ed25519PrivateKey.generate()
    trusted_key = Ed25519PrivateKey.generate()
    signing_fingerprint = ed25519_public_key_fingerprint(signing_key.public_key())
    trusted_fingerprint = ed25519_public_key_fingerprint(trusted_key.public_key())

    with TestClient(app) as client:
        headers = _login(client)
        installed = client.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(signing_key)},
            headers=headers,
        )
        assert installed.status_code == 200

        signing_path = app.state.settings.bundle_signing_private_key_file
        signing_before = signing_path.read_bytes()

        _switch(client, headers, PortalContour.TARGET)
        added = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(trusted_key), "confirm": True},
            headers=headers,
        )
        assert added.status_code == 201
        trust_before = _directory_snapshot(app.state.settings.bundle_trusted_public_keys_dir)

        target_status = client.get("/api/settings/keys", headers=headers)
        assert target_status.status_code == 200
        assert target_status.json()["contour"] == "TARGET"
        assert target_status.json()["signing_key"] is None
        assert target_status.json()["trusted_keys"] == [
            {"fingerprint": trusted_fingerprint, "enabled": True}
        ]

        _switch(client, headers, PortalContour.SOURCE)
        source_status = client.get("/api/settings/keys", headers=headers)
        assert source_status.status_code == 200
        assert source_status.json()["contour"] == "SOURCE"
        assert source_status.json()["signing_key"] == {
            "configured": True,
            "fingerprint": signing_fingerprint,
        }
        assert source_status.json()["trusted_keys"] == []

        _switch(client, headers, PortalContour.TARGET)
        assert signing_path.read_bytes() == signing_before
        assert (
            _directory_snapshot(app.state.settings.bundle_trusted_public_keys_dir)
            == trust_before
        )

    with app.state.session_factory() as session:
        key_events = list(
            session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type.in_(
                        ["signing.key.installed", "trust.key.added"]
                    )
                )
                .order_by(AuditEvent.id)
            )
        )
    assert [event.event_type for event in key_events] == [
        "signing.key.installed",
        "trust.key.added",
    ]
    assert json.loads(key_events[0].metadata_json)["runtime_mode"] == "SOURCE"
    assert json.loads(key_events[1].metadata_json)["runtime_mode"] == "TARGET"
    assert all("PRIVATE KEY" not in event.metadata_json for event in key_events)


def test_mode_specific_key_mutations_fail_closed_after_switch(tmp_path: Path) -> None:
    app = _app(tmp_path)
    signing_key = Ed25519PrivateKey.generate()
    replacement_signing_key = Ed25519PrivateKey.generate()
    trusted_key = Ed25519PrivateKey.generate()

    with TestClient(app) as client:
        headers = _login(client)
        assert (
            client.put(
                "/api/settings/keys/signing",
                json={"pem": _private_pem(signing_key)},
                headers=headers,
            ).status_code
            == 200
        )
        signing_path = app.state.settings.bundle_signing_private_key_file
        signing_before = signing_path.read_bytes()

        _switch(client, headers, PortalContour.TARGET)
        blocked_signing = client.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(replacement_signing_key)},
            headers=headers,
        )
        assert blocked_signing.status_code == 409
        assert (
            blocked_signing.json()["error"]["code"]
            == "key_management_wrong_contour"
        )
        assert signing_path.read_bytes() == signing_before

        added = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(trusted_key), "confirm": True},
            headers=headers,
        )
        assert added.status_code == 201
        trust_before = _directory_snapshot(app.state.settings.bundle_trusted_public_keys_dir)

        _switch(client, headers, PortalContour.SOURCE)
        blocked_trust = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(Ed25519PrivateKey.generate()), "confirm": True},
            headers=headers,
        )
        assert blocked_trust.status_code == 409
        assert (
            blocked_trust.json()["error"]["code"]
            == "key_management_wrong_contour"
        )
        assert (
            _directory_snapshot(app.state.settings.bundle_trusted_public_keys_dir)
            == trust_before
        )


def test_target_verification_uses_only_explicit_trust_not_local_signing_key(tmp_path: Path) -> None:
    app = _app(tmp_path)
    bundle_signer = Ed25519PrivateKey.generate()
    local_signer_after_rotation = Ed25519PrivateKey.generate()
    trusted_unrelated = Ed25519PrivateKey.generate()
    bundle_fingerprint = ed25519_public_key_fingerprint(bundle_signer.public_key())

    with TestClient(app) as client:
        headers = _login(client)
        installed = client.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(bundle_signer)},
            headers=headers,
        )
        assert installed.status_code == 200
        bundle = _build_bundle(app.state.settings, "DELIVERY-20260915-KEYISO1")

        rotated = client.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(local_signer_after_rotation)},
            headers=headers,
        )
        assert rotated.status_code == 200
        local_signing_bytes = app.state.settings.bundle_signing_private_key_file.read_bytes()
        assert _private_pem(local_signer_after_rotation).encode() == local_signing_bytes

        _switch(client, headers, PortalContour.TARGET)
        unrelated = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(trusted_unrelated), "confirm": True},
            headers=headers,
        )
        assert unrelated.status_code == 201

        # The local SOURCE private key must never become an implicit TARGET trust anchor.
        failed = False
        try:
            BundlePackageService(app.state.settings).verify_bundle(bundle.archive_path)
        except Exception:
            failed = True
        assert failed is True

        trusted = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(bundle_signer), "confirm": True},
            headers=headers,
        )
        assert trusted.status_code == 201
        verified = BundlePackageService(app.state.settings).verify_bundle(bundle.archive_path)
        assert verified.signing_key_fingerprint == bundle_fingerprint

        _switch(client, headers, PortalContour.SOURCE)
        # TARGET trust material remains present but cannot influence SOURCE signing.
        second = _build_bundle(app.state.settings, "DELIVERY-20260915-KEYISO2")
        expected_local = ed25519_public_key_fingerprint(local_signer_after_rotation.public_key())
        assert second.signing_key_fingerprint == expected_local
        assert second.signing_key_fingerprint != bundle_fingerprint
