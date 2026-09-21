from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
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
from app.services import key_management as key_management_module
from app.services.bundle_package_service import (
    BundlePackageError,
    BundlePackageService,
    HelmChartPackageInput,
)
from app.services.key_management import (
    KeyManagementError,
    KeyManagementService,
    TrustedKeyStatus,
)
from app.services.key_material import ed25519_public_key_fingerprint

JWT_TEST_KEY = "key-hardening-test-key-" + "x" * 32


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
    root.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{root / 'portal.db'}"
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


def _build_chart_bundle(settings: Settings, delivery_id: str):
    payload = settings.bundle_payload_root / "packages" / f"{delivery_id}.tgz"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"key-hardening-chart-" + delivery_id.encode("ascii"))
    return BundlePackageService(settings).build_bundle(
        source=BundleSource(
            contour="SOURCE",
            harbor="harbor.source.local",
            portal_version="test",
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


def test_generated_identity_stays_successful_after_post_commit_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        bundle_signing_private_key_file=tmp_path / "keys" / "source-signing-private.pem",
    )
    service = KeyManagementService(settings)

    def fail_directory_fsync(_path: Path) -> None:
        raise OSError("synthetic directory fsync failure")

    monkeypatch.setattr(service, "_fsync_directory", fail_directory_fsync)

    mutation = service.generate_signing_private_key()

    assert mutation.action == "generated"
    status = service.signing_status()
    assert status.configured is True
    assert status.fingerprint == mutation.fingerprint


def test_concurrent_source_generation_never_silently_rotates_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        bundle_signing_private_key_file=tmp_path / "keys" / "source-signing-private.pem",
    )
    first = KeyManagementService(settings)
    second = KeyManagementService(settings)
    barrier = threading.Barrier(2)
    original_create = KeyManagementService._atomic_create_signing_key

    def synchronized_create(service: KeyManagementService, payload: bytes) -> None:
        barrier.wait(timeout=5)
        original_create(service, payload)

    monkeypatch.setattr(
        KeyManagementService,
        "_atomic_create_signing_key",
        synchronized_create,
    )

    def generate(service: KeyManagementService):
        try:
            return service.generate_signing_private_key()
        except KeyManagementError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(generate, (first, second)))

    mutations = [item for item in outcomes if not isinstance(item, KeyManagementError)]
    errors = [item for item in outcomes if isinstance(item, KeyManagementError)]
    assert len(mutations) == 1
    assert len(errors) == 1
    assert mutations[0].action == "generated"
    assert errors[0].code == "signing_key_already_configured"

    status = KeyManagementService(settings).signing_status()
    assert status.configured is True
    assert status.fingerprint == mutations[0].fingerprint


def test_target_mutations_require_explicit_server_confirmation(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.TARGET)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())

    with TestClient(app) as client:
        headers = _login(client)

        denied_add = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(old_key)},
            headers=headers,
        )
        assert denied_add.status_code == 409
        assert denied_add.json()["error"]["code"] == "trusted_key_confirmation_required"
        assert not app.state.settings.bundle_trusted_public_keys_dir.exists()

        added = client.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(old_key), "confirm": True},
            headers=headers,
        )
        assert added.status_code == 201

        denied_state = client.patch(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            json={"enabled": False},
            headers=headers,
        )
        denied_replace = client.put(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            json={"pem": _public_pem(new_key)},
            headers=headers,
        )
        denied_remove = client.delete(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            headers=headers,
        )
        for denied in (denied_state, denied_replace, denied_remove):
            assert denied.status_code == 409
            assert denied.json()["error"]["code"] == "trusted_key_confirmation_required"

        listed = client.get("/api/settings/keys", headers=headers)
        assert listed.status_code == 200
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


def test_replace_at_full_limit_switches_real_verifier_trust(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    target_app = _build_app(tmp_path, PortalContour.TARGET)
    target_app.state.settings.bundle_max_trusted_keys = 1
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())

    with TestClient(source_app) as source:
        headers = _login(source)
        assert source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(old_key)},
            headers=headers,
        ).status_code == 200
        old_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-REPL01",
        )
        assert source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(new_key)},
            headers=headers,
        ).status_code == 200
        new_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-REPL02",
        )

    with TestClient(target_app) as target:
        headers = _login(target)
        added = target.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(old_key), "confirm": True},
            headers=headers,
        )
        assert added.status_code == 201

        over_limit = target.post(
            "/api/settings/keys/trusted",
            json={"pem": _public_pem(new_key), "confirm": True},
            headers=headers,
        )
        assert over_limit.status_code == 409
        assert over_limit.json()["error"]["code"] == "trusted_key_limit_exceeded"

        replaced = target.put(
            f"/api/settings/keys/trusted/{old_fingerprint}",
            json={"pem": _public_pem(new_key), "confirm": True},
            headers=headers,
        )
        assert replaced.status_code == 200
        assert replaced.json() == {
            "action": "replaced",
            "fingerprint": new_fingerprint,
        }

        listed = target.get("/api/settings/keys", headers=headers)
        assert listed.json()["trusted_keys"] == [
            {"fingerprint": new_fingerprint, "enabled": True}
        ]

        verifier = BundlePackageService(target_app.state.settings)
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
    assert metadata["previous_fingerprint"] == old_fingerprint
    assert metadata["fingerprint"] == new_fingerprint
    assert "BEGIN PUBLIC KEY" not in event.metadata_json


def test_replace_publish_failure_keeps_previous_key_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.TARGET,
        bundle_trusted_public_keys_dir=tmp_path / "trusted",
        bundle_max_trusted_keys=1,
    )
    service = KeyManagementService(settings)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())
    service.add_trusted_public_key(_public_pem(old_key))

    def fail_atomic_write(_path: Path, _payload: bytes, _mode: int) -> None:
        raise KeyManagementError("key_store_write_failed", "synthetic write failure")

    monkeypatch.setattr(service, "_atomic_write", fail_atomic_write)
    with pytest.raises(KeyManagementError) as exc_info:
        service.replace_trusted_public_key(old_fingerprint, _public_pem(new_key))
    assert exc_info.value.code == "key_store_write_failed"
    assert service.list_trusted_keys() == (
        TrustedKeyStatus(fingerprint=old_fingerprint, enabled=True),
    )
    assert not service._enabled_path(new_fingerprint).exists()


def test_replace_preserves_disabled_state(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.TARGET,
        bundle_trusted_public_keys_dir=tmp_path / "trusted",
        bundle_max_trusted_keys=1,
    )
    service = KeyManagementService(settings)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())
    service.add_trusted_public_key(_public_pem(old_key))
    service.set_trusted_key_enabled(old_fingerprint, False)

    mutation = service.replace_trusted_public_key(old_fingerprint, _public_pem(new_key))

    assert mutation.fingerprint == new_fingerprint
    assert service.list_trusted_keys() == (
        TrustedKeyStatus(fingerprint=new_fingerprint, enabled=False),
    )
    assert not list(settings.bundle_trusted_public_keys_dir.glob("*.pem"))


def test_post_commit_canonicalization_failure_keeps_new_key_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.TARGET,
        bundle_trusted_public_keys_dir=tmp_path / "trusted",
        bundle_max_trusted_keys=1,
    )
    service = KeyManagementService(settings)
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())
    new_fingerprint = ed25519_public_key_fingerprint(new_key.public_key())
    service.add_trusted_public_key(_public_pem(old_key))

    real_replace = key_management_module.os.replace
    replace_calls = 0

    def fail_second_replace(source: Path | str, target: Path | str) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("synthetic canonicalization failure")
        real_replace(source, target)

    monkeypatch.setattr(key_management_module.os, "replace", fail_second_replace)
    mutation = service.replace_trusted_public_key(old_fingerprint, _public_pem(new_key))

    assert mutation.fingerprint == new_fingerprint
    assert replace_calls == 2
    assert service.list_trusted_keys() == (
        TrustedKeyStatus(fingerprint=new_fingerprint, enabled=True),
    )
    assert len(list(settings.bundle_trusted_public_keys_dir.glob("*.pem"))) == 1
