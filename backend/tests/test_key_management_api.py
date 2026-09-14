from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
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
from app.services.key_material import ed25519_public_key_fingerprint

JWT_TEST_KEY = "key-management-test-key-" + "x" * 32


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


def _build_app(tmp_path: Path, contour: PortalContour):
    root = tmp_path / contour.value.lower()
    database_url = f"sqlite:///{root / 'portal.db'}"
    root.mkdir(parents=True, exist_ok=True)
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=JWT_TEST_KEY,
        portal_contour=contour,
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


def _build_chart_bundle(settings: Settings, delivery_id: str):
    payload = settings.bundle_payload_root / "packages" / f"{delivery_id}.tgz"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"synthetic-chart-package-" + delivery_id.encode("ascii"))
    service = BundlePackageService(settings)
    return service.build_bundle(
        source=BundleSource(
            contour="SOURCE",
            harbor="harbor.source.local",
            portal_version="0.1.0",
        ),
        created_by="admin",
        artifacts=[
            HelmChartPackageInput(
                repository="project/charts",
                name="sample",
                version="1.2.3",
                source_digest=None,
                source_path=payload,
                payload_path=f"charts/{delivery_id}.tgz",
            )
        ],
        delivery_id=delivery_id,
        created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
    )


def test_key_settings_and_mutations_are_admin_only(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    with TestClient(source_app) as client:
        tokens = {role: _login(client, role) for role in ("admin", "operator", "viewer")}
        assert client.get("/api/settings/keys", headers=_auth(tokens["admin"])).status_code == 200
        for role in ("operator", "viewer"):
            assert client.get("/api/settings/keys", headers=_auth(tokens[role])).status_code == 403
            denied = client.put(
                "/api/settings/keys/signing",
                json={"pem": _private_pem(Ed25519PrivateKey.generate())},
                headers=_auth(tokens[role]),
            )
            assert denied.status_code == 403

    target_app = _build_app(tmp_path, PortalContour.TARGET)
    with TestClient(target_app) as client:
        tokens = {role: _login(client, role) for role in ("admin", "operator", "viewer")}
        for role in ("operator", "viewer"):
            denied = client.post(
                "/api/settings/keys/trusted",
                json={"pem": _public_pem(Ed25519PrivateKey.generate())},
                headers=_auth(tokens[role]),
            )
            assert denied.status_code == 403


def test_source_private_key_is_atomic_private_and_never_disclosed(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.SOURCE)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_pem = _private_pem(old_key)
    new_pem = _private_pem(new_key)
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())

    with TestClient(app) as client:
        admin = _login(client, "admin")
        headers = _auth(admin)

        empty = client.get("/api/settings/keys", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["signing_key"] == {"configured": False, "fingerprint": None}

        installed = client.put(
            "/api/settings/keys/signing",
            json={"pem": old_pem},
            headers=headers,
        )
        assert installed.status_code == 200
        assert installed.json() == {"action": "installed", "fingerprint": old_fingerprint}
        assert old_pem not in installed.text

        key_path = app.state.settings.bundle_signing_private_key_file
        assert key_path.is_file()
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

        status_response = client.get("/api/settings/keys", headers=headers)
        assert status_response.json()["signing_key"] == {
            "configured": True,
            "fingerprint": old_fingerprint,
        }
        assert old_pem not in status_response.text

        rotated = client.put(
            "/api/settings/keys/signing",
            json={"pem": new_pem},
            headers=headers,
        )
        assert rotated.status_code == 200
        assert rotated.json() == {"action": "rotated", "fingerprint": new_fingerprint}
        assert old_fingerprint != new_fingerprint
        assert old_pem.encode() not in key_path.read_bytes()

    with app.state.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.in_(["signing.key.installed", "signing.key.rotated"]))
                .order_by(AuditEvent.id)
            )
        )
    assert [event.event_type for event in events] == [
        "signing.key.installed",
        "signing.key.rotated",
    ]
    assert json.loads(events[0].metadata_json)["fingerprint"] == old_fingerprint
    assert json.loads(events[1].metadata_json)["fingerprint"] == new_fingerprint
    assert all(
        old_pem not in event.metadata_json and new_pem not in event.metadata_json
        for event in events
    )


def test_wrong_key_types_and_size_bounds_are_rejected_without_replacement(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    valid_key = Ed25519PrivateKey.generate()
    with TestClient(source_app) as source:
        admin = _login(source, "admin")
        headers = _auth(admin)
        installed = source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(valid_key)},
            headers=headers,
        )
        assert installed.status_code == 200
        before = source_app.state.settings.bundle_signing_private_key_file.read_bytes()

        public_as_private = source.put(
            "/api/settings/keys/signing",
            json={"pem": _public_pem(valid_key)},
            headers=headers,
        )
        assert public_as_private.status_code == 422
        assert public_as_private.json()["error"]["code"] == "signing_key_invalid"
        assert source_app.state.settings.bundle_signing_private_key_file.read_bytes() == before

        source_app.state.settings.bundle_key_material_max_bytes = 1024
        oversized = source.put(
            "/api/settings/keys/signing",
            json={"pem": "x" * 1025},
            headers=headers,
        )
        assert oversized.status_code == 422
        assert oversized.json()["error"]["code"] == "key_material_size_invalid"
        assert source_app.state.settings.bundle_signing_private_key_file.read_bytes() == before

    target_app = _build_app(tmp_path, PortalContour.TARGET)
    with TestClient(target_app) as target:
        admin = _login(target, "admin")
        private_as_public = target.post(
            "/api/settings/keys/trusted",
            json={"pem": _private_pem(valid_key)},
            headers=_auth(admin),
            params={"confirm": True},
        )
        assert private_as_public.status_code == 422
        assert private_as_public.json()["error"]["code"] == "trusted_key_invalid"
        assert not target_app.state.settings.bundle_trusted_public_keys_dir.exists()


def test_target_mutations_require_server_side_confirmation_without_side_effects(
    tmp_path: Path,
) -> None:
    app = _build_app(tmp_path, PortalContour.TARGET)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    trust_dir = app.state.settings.bundle_trusted_public_keys_dir

    with TestClient(app) as client:
        admin = _login(client, "admin")
        headers = _auth(admin)

        denied_add = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(old_key)},
            headers=headers,
        )
        assert denied_add.status_code == 409
        assert denied_add.json()["error"]["code"] == "key_mutation_confirmation_required"
        assert not trust_dir.exists()

        added = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(old_key)},
            headers=headers,
            params={"confirm": True},
        )
        assert added.status_code == 201

        denied_state = client.patch(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            json={"enabled": False},
            headers=headers,
            params={"confirm": False},
        )
        assert denied_state.status_code == 409
        assert denied_state.json()["error"]["code"] == "key_mutation_confirmation_required"

        denied_replace = client.put(
            f"/api/settings/keys/trusted/{old_fingerprint}/replace",
            json={"pem": _public_pem(new_key)},
            headers=headers,
        )
        assert denied_replace.status_code == 409
        assert denied_replace.json()["error"]["code"] == "key_mutation_confirmation_required"

        denied_remove = client.delete(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            headers=headers,
            params={"confirm": False},
        )
        assert denied_remove.status_code == 409
        assert denied_remove.json()["error"]["code"] == "key_mutation_confirmation_required"

        listed = client.get("/api/settings/keys", headers=headers)
        assert listed.json()["trusted_keys"] == [
            {"fingerprint": old_fingerprint, "enabled": True}
        ]

    with app.state.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.like("trust.key.%"))
                .order_by(AuditEvent.id)
            )
        )
    assert [event.event_type for event in events] == ["trust.key.added"]


def test_target_replace_works_at_full_limit_and_updates_real_verifier(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    target_app = _build_app(tmp_path, PortalContour.TARGET)
    target_app.state.settings.bundle_max_trusted_keys = 1
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_pem = _public_pem(old_key)
    new_pem = _public_pem(new_key)
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())

    with TestClient(source_app) as source:
        headers = _auth(_login(source, "admin"))
        assert source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(old_key)},
            headers=headers,
        ).status_code == 200
        old_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-REPLACEOLD1",
        )
        assert source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(new_key)},
            headers=headers,
        ).status_code == 200
        new_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-REPLACENEW1",
        )

    with TestClient(target_app) as target:
        headers = _auth(_login(target, "admin"))
        added = target.post(
            "/api/settings/keys/trusted",
            json={"pem": old_pem},
            headers=headers,
            params={"confirm": True},
        )
        assert added.status_code == 201

        verifier = BundlePackageService(target_app.state.settings)
        assert verifier.verify_bundle(
            old_bundle.archive_path,
            sidecar_path=old_bundle.sidecar_path,
        ).signing_key_fingerprint == old_fingerprint

        replaced = target.put(
            f"/api/settings/keys/trusted/{old_fingerprint}/replace",
            json={"pem": new_pem},
            headers=headers,
            params={"confirm": True},
        )
        assert replaced.status_code == 200
        assert replaced.json() == {"action": "replaced", "fingerprint": new_fingerprint}

        listed = target.get("/api/settings/keys", headers=headers)
        assert listed.json()["trusted_keys"] == [
            {"fingerprint": new_fingerprint, "enabled": True}
        ]

        with pytest.raises(BundlePackageError) as exc_info:
            verifier.verify_bundle(
                old_bundle.archive_path,
                sidecar_path=old_bundle.sidecar_path,
            )
        assert exc_info.value.code == "bundle_signature_untrusted"
        assert verifier.verify_bundle(
            new_bundle.archive_path,
            sidecar_path=new_bundle.sidecar_path,
        ).signing_key_fingerprint == new_fingerprint

    with target_app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "trust.key.replaced")
            .order_by(AuditEvent.id.desc())
        )
    assert event is not None
    metadata = json.loads(event.metadata_json)
    assert metadata["old_fingerprint"] == old_fingerprint
    assert metadata["new_fingerprint"] == new_fingerprint
    assert old_pem not in event.metadata_json
    assert new_pem not in event.metadata_json


def test_target_overlap_rotation_is_consumed_by_real_bundle_verifier(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    target_app = _build_app(tmp_path, PortalContour.TARGET)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())

    with TestClient(source_app) as source:
        source_admin = _login(source, "admin")
        headers = _auth(source_admin)
        assert source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(old_key)},
            headers=headers,
        ).status_code == 200
        old_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-OLDKEY1",
        )
        assert old_bundle.signing_key_fingerprint == old_fingerprint

        assert source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(new_key)},
            headers=headers,
        ).status_code == 200
        new_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-NEWKEY1",
        )
        assert new_bundle.signing_key_fingerprint == new_fingerprint

    with TestClient(target_app) as target:
        target_admin = _login(target, "admin")
        headers = _auth(target_admin)
        for key in (old_key, new_key):
            added = target.post(
                "/api/settings/keys/trusted",
                json={"pem": _public_pem(key)},
                headers=headers,
                params={"confirm": True},
            )
            assert added.status_code == 201

        listed = target.get("/api/settings/keys", headers=headers)
        assert listed.status_code == 200
        expected_trusted = sorted(
            [
                {"fingerprint": old_fingerprint, "enabled": True},
                {"fingerprint": new_fingerprint, "enabled": True},
            ],
            key=lambda item: item["fingerprint"],
        )
        assert listed.json()["trusted_keys"] == expected_trusted

        verifier = BundlePackageService(target_app.state.settings)
        assert verifier.verify_bundle(
            old_bundle.archive_path,
            sidecar_path=old_bundle.sidecar_path,
        ).signing_key_fingerprint == old_fingerprint
        assert verifier.verify_bundle(
            new_bundle.archive_path,
            sidecar_path=new_bundle.sidecar_path,
        ).signing_key_fingerprint == new_fingerprint

        disabled = target.patch(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            json={"enabled": False},
            headers=headers,
            params={"confirm": True},
        )
        assert disabled.status_code == 200
        assert disabled.json()["action"] == "disabled"

        with pytest.raises(BundlePackageError) as exc_info:
            verifier.verify_bundle(
                old_bundle.archive_path,
                sidecar_path=old_bundle.sidecar_path,
            )
        assert exc_info.value.code == "bundle_signature_untrusted"
        assert verifier.verify_bundle(
            new_bundle.archive_path,
            sidecar_path=new_bundle.sidecar_path,
        ).signing_key_fingerprint == new_fingerprint

        enabled = target.patch(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            json={"enabled": True},
            headers=headers,
            params={"confirm": True},
        )
        assert enabled.status_code == 200
        assert verifier.verify_bundle(
            old_bundle.archive_path,
            sidecar_path=old_bundle.sidecar_path,
        ).signing_key_fingerprint == old_fingerprint

        removed = target.delete(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            headers=headers,
            params={"confirm": True},
        )
        assert removed.status_code == 200
        assert removed.json()["action"] == "removed"

    trust_dir = target_app.state.settings.bundle_trusted_public_keys_dir
    active_files = sorted(path.name for path in trust_dir.glob("*.pem"))
    assert active_files == [f"{new_fingerprint.removeprefix('sha256:')}.pem"]
    assert all(not path.is_symlink() for path in trust_dir.iterdir())
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in trust_dir.iterdir())

    with target_app.state.session_factory() as session:
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
        "trust.key.removed",
    ]
    assert all("BEGIN PUBLIC KEY" not in event.metadata_json for event in events)


def test_target_managed_actions_canonicalize_legacy_filename(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.TARGET)
    key = Ed25519PrivateKey.generate()
    fingerprint = ed25519_public_key_fingerprint(key.public_key())
    trust_dir = app.state.settings.bundle_trusted_public_keys_dir
    trust_dir.mkdir(parents=True, exist_ok=True)
    legacy = trust_dir / "source-legacy.pem"
    legacy.write_text(_public_pem(key), encoding="utf-8")
    os.chmod(legacy, 0o600)

    with TestClient(app) as client:
        admin = _login(client, "admin")
        headers = _auth(admin)
        listed = client.get("/api/settings/keys", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["trusted_keys"] == [
            {"fingerprint": fingerprint, "enabled": True}
        ]

        disabled = client.patch(
            f"/api/settings/keys/trusted/{fingerprint}",
            json={"enabled": False},
            headers=headers,
            params={"confirm": True},
        )
        assert disabled.status_code == 200

    assert not legacy.exists()
    canonical_disabled = trust_dir / f"{fingerprint.removeprefix('sha256:')}.disabled"
    assert canonical_disabled.is_file()
    assert not list(trust_dir.glob("*.pem"))
