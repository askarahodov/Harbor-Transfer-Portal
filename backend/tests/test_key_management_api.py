from __future__ import annotations

import io
import json
import os
import stat
import tarfile
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
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import BundleSource, OperationStatus, OperationType
from app.main import create_app
from app.services.bundle_package_service import (
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


def _trusted_payload(key: Ed25519PrivateKey) -> dict[str, object]:
    return {"pem": _public_pem(key), "confirm": True}


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
        bundle_pending_signing_private_key_file=(
            root / "keys" / "source-signing-pending-private.pem"
        ),
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
                json=_trusted_payload(Ed25519PrivateKey.generate()),
                headers=_auth(tokens[role]),
            )
            assert denied.status_code == 403


def test_admin_can_generate_identity_and_export_only_public_key(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.SOURCE)

    with TestClient(app) as client:
        admin = _login(client, "admin")
        operator = _login(client, "operator")
        headers = _auth(admin)

        denied_generate = client.post(
            "/api/settings/keys/signing/generate",
            headers=_auth(operator),
        )
        assert denied_generate.status_code == 403

        generated = client.post("/api/settings/keys/signing/generate", headers=headers)
        assert generated.status_code == 201
        payload = generated.json()
        assert payload["action"] == "generated"
        fingerprint = payload["fingerprint"]
        assert fingerprint.startswith("sha256:")

        key_path = app.state.settings.bundle_signing_private_key_file
        private_bytes = key_path.read_bytes()
        assert b"PRIVATE KEY" in private_bytes
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        assert private_bytes not in generated.content

        public_response = client.get("/api/settings/keys/signing/public", headers=headers)
        assert public_response.status_code == 200
        assert public_response.headers["cache-control"] == "no-store"
        assert public_response.headers["x-signing-key-fingerprint"] == fingerprint
        assert "source-signing-public.pem" in public_response.headers["content-disposition"]
        assert b"PUBLIC KEY" in public_response.content
        assert b"PRIVATE KEY" not in public_response.content

        public_key = serialization.load_pem_public_key(public_response.content)
        assert ed25519_public_key_fingerprint(public_key) == fingerprint

        denied_public = client.get(
            "/api/settings/keys/signing/public",
            headers=_auth(operator),
        )
        assert denied_public.status_code == 403

        duplicate = client.post("/api/settings/keys/signing/generate", headers=headers)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "signing_key_already_configured"
        assert key_path.read_bytes() == private_bytes

    with app.state.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == "signing.key.generated")
                .order_by(AuditEvent.id)
            )
        )
    assert len(events) == 1
    assert events[0].actor_username == "admin"
    metadata = json.loads(events[0].metadata_json)
    assert metadata["action"] == "generated"
    assert metadata["fingerprint"] == fingerprint
    assert "PRIVATE KEY" not in events[0].metadata_json


def test_source_trust_package_bootstraps_target_verifier_end_to_end(
    tmp_path: Path,
) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    target_app = _build_app(tmp_path, PortalContour.TARGET)

    with TestClient(source_app) as source:
        source_admin = _login(source, "admin")
        source_headers = _auth(source_admin)
        generated = source.post(
            "/api/settings/keys/signing/generate",
            headers=source_headers,
        )
        assert generated.status_code == 201
        fingerprint = generated.json()["fingerprint"]

        first = source.get(
            "/api/settings/keys/signing/trust-package",
            headers=source_headers,
        )
        second = source.get(
            "/api/settings/keys/signing/trust-package",
            headers=source_headers,
        )
        assert first.status_code == 200
        assert first.content == second.content
        assert first.headers["x-signing-key-fingerprint"] == fingerprint
        assert "htp-trust.tar.gz" in first.headers["content-disposition"]

        with tarfile.open(fileobj=io.BytesIO(first.content), mode="r:gz") as archive:
            names = [member.name for member in archive.getmembers()]
            assert names == [
                "source-signing-public.pem",
                "identity.json",
                "fingerprint.sha256",
            ]
            contents = {
                name: archive.extractfile(name).read()  # type: ignore[union-attr]
                for name in names
            }
        assert b"PRIVATE KEY" not in b"".join(contents.values())
        assert contents["fingerprint.sha256"] == f"{fingerprint}\n".encode("ascii")

        bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260921-TRUSTPKG1",
        )

    with TestClient(target_app) as target:
        admin = _login(target, "admin")
        operator = _login(target, "operator")

        denied = target.post(
            "/api/settings/keys/trusted/package",
            params={"confirm": True},
            content=first.content,
            headers={
                **_auth(operator),
                "Content-Type": "application/gzip",
            },
        )
        assert denied.status_code == 403

        unconfirmed = target.post(
            "/api/settings/keys/trusted/package",
            content=first.content,
            headers={
                **_auth(admin),
                "Content-Type": "application/gzip",
            },
        )
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["error"]["code"] == "trusted_key_confirmation_required"

        imported = target.post(
            "/api/settings/keys/trusted/package",
            params={"confirm": True},
            content=first.content,
            headers={
                **_auth(admin),
                "Content-Type": "application/gzip",
            },
        )
        assert imported.status_code == 201
        assert imported.json() == {"action": "added", "fingerprint": fingerprint}

        duplicate = target.post(
            "/api/settings/keys/trusted/package",
            params={"confirm": True},
            content=first.content,
            headers={
                **_auth(admin),
                "Content-Type": "application/gzip",
            },
        )
        assert duplicate.status_code == 201
        assert duplicate.json() == {"action": "unchanged", "fingerprint": fingerprint}

        settings_response = target.get(
            "/api/settings/keys",
            headers=_auth(admin),
        )
        assert settings_response.status_code == 200
        assert settings_response.json()["trusted_keys"] == [
            {"fingerprint": fingerprint, "enabled": True}
        ]

    verified = BundlePackageService(target_app.state.settings).verify_bundle(
        bundle.archive_path,
        sidecar_path=bundle.sidecar_path,
    )
    assert verified.signing_key_fingerprint == fingerprint

    with source_app.state.session_factory() as session:
        exported = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "signing.trust_package.exported"
                )
            )
        )
    assert len(exported) == 2
    assert all(event.actor_username == "admin" for event in exported)

    with target_app.state.session_factory() as session:
        imported_events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == "trust.source_identity.imported")
                .order_by(AuditEvent.id)
            )
        )
    assert [json.loads(event.metadata_json)["action"] for event in imported_events] == [
        "added",
        "unchanged",
    ]
    assert all(event.actor_username == "admin" for event in imported_events)


def test_source_private_key_is_atomic_private_and_never_disclosed(tmp_path: Path) -> None:
    app = _build_app(tmp_path, PortalContour.SOURCE)
    old_key = Ed25519PrivateKey.generate()
    old_pem = _private_pem(old_key)
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())

    with TestClient(app) as client:
        admin = _login(client, "admin")
        headers = _auth(admin)

        empty = client.get("/api/settings/keys", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["signing_key"] == {"configured": False, "fingerprint": None}
        assert empty.json()["pending_signing_key"] == {
            "configured": False,
            "fingerprint": None,
        }

        installed = client.put(
            "/api/settings/keys/signing",
            json={"pem": old_pem},
            headers=headers,
        )
        assert installed.status_code == 200
        assert installed.json() == {"action": "installed", "fingerprint": old_fingerprint}

        key_path = app.state.settings.bundle_signing_private_key_file
        pending_path = app.state.settings.bundle_pending_signing_private_key_file
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

        direct_rotation = client.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(Ed25519PrivateKey.generate())},
            headers=headers,
        )
        assert direct_rotation.status_code == 409
        assert direct_rotation.json()["error"]["code"] == "signing_rotation_required"

        prepared = client.post(
            "/api/settings/keys/signing/rotation/prepare",
            headers=headers,
        )
        assert prepared.status_code == 201
        pending_fingerprint = prepared.json()["fingerprint"]
        assert pending_fingerprint != old_fingerprint
        assert pending_path.is_file()
        assert stat.S_IMODE(pending_path.stat().st_mode) == 0o600

        duplicate = client.post(
            "/api/settings/keys/signing/rotation/prepare",
            headers=headers,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == (
            "pending_signing_key_already_configured"
        )

        pending_package = client.get(
            "/api/settings/keys/signing/rotation/trust-package",
            headers=headers,
        )
        assert pending_package.status_code == 200
        assert pending_package.headers["x-signing-key-fingerprint"] == pending_fingerprint
        assert b"PRIVATE KEY" not in pending_package.content

        status_response = client.get("/api/settings/keys", headers=headers)
        assert status_response.json()["signing_key"]["fingerprint"] == old_fingerprint
        assert status_response.json()["pending_signing_key"] == {
            "configured": True,
            "fingerprint": pending_fingerprint,
        }

        mismatch = client.post(
            "/api/settings/keys/signing/rotation/activate",
            json={"expected_fingerprint": "sha256:" + "0" * 64},
            headers=headers,
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == (
            "pending_signing_key_fingerprint_mismatch"
        )
        assert pending_path.exists()

        activated = client.post(
            "/api/settings/keys/signing/rotation/activate",
            json={"expected_fingerprint": pending_fingerprint},
            headers=headers,
        )
        assert activated.status_code == 200
        assert activated.json()["action"] == "activated"
        assert activated.json()["fingerprint"] == pending_fingerprint
        assert not pending_path.exists()
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

        final_status = client.get("/api/settings/keys", headers=headers).json()
        assert final_status["signing_key"]["fingerprint"] == pending_fingerprint
        assert final_status["pending_signing_key"] == {
            "configured": False,
            "fingerprint": None,
        }

    with app.state.session_factory() as session:
        event_types = list(
            session.scalars(
                select(AuditEvent.event_type)
                .where(AuditEvent.event_type.like("signing.%"))
                .order_by(AuditEvent.id)
            )
        )
    assert "signing.key.installed" in event_types
    assert "signing.rotation.prepared" in event_types
    assert "signing.rotation.trust_package.exported" in event_types
    assert "signing.rotation.activated" in event_types


def test_wrong_key_types_and_size_bounds_are_rejected_without_replacement(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    valid_key = Ed25519PrivateKey.generate()
    with TestClient(source_app) as source:
        admin = _login(source, "admin")
        headers = _auth(admin)

        public_as_private = source.put(
            "/api/settings/keys/signing",
            json={"pem": _public_pem(valid_key)},
            headers=headers,
        )
        assert public_as_private.status_code == 422
        assert public_as_private.json()["error"]["code"] == "signing_key_invalid"
        assert not source_app.state.settings.bundle_signing_private_key_file.exists()

        source_app.state.settings.bundle_key_material_max_bytes = 1024
        oversized = source.put(
            "/api/settings/keys/signing",
            json={"pem": "x" * 1025},
            headers=headers,
        )
        assert oversized.status_code == 422
        assert oversized.json()["error"]["code"] == "key_material_size_invalid"
        assert not source_app.state.settings.bundle_signing_private_key_file.exists()

        installed = source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(valid_key)},
            headers=headers,
        )
        assert installed.status_code == 200
        before = source_app.state.settings.bundle_signing_private_key_file.read_bytes()

        direct_rotation = source.put(
            "/api/settings/keys/signing",
            json={"pem": _private_pem(Ed25519PrivateKey.generate())},
            headers=headers,
        )
        assert direct_rotation.status_code == 409
        assert direct_rotation.json()["error"]["code"] == "signing_rotation_required"
        assert source_app.state.settings.bundle_signing_private_key_file.read_bytes() == before

    target_app = _build_app(tmp_path, PortalContour.TARGET)
    with TestClient(target_app) as target:
        admin = _login(target, "admin")
        private_as_public = target.post(
            "/api/settings/keys/trusted",
            json={"pem": _private_pem(valid_key), "confirm": True},
            headers=_auth(admin),
        )
        assert private_as_public.status_code == 422
        assert private_as_public.json()["error"]["code"] == "trusted_key_invalid"
        assert not target_app.state.settings.bundle_trusted_public_keys_dir.exists()


def test_target_overlap_rotation_is_consumed_by_real_bundle_verifier(tmp_path: Path) -> None:
    source_app = _build_app(tmp_path, PortalContour.SOURCE)
    target_app = _build_app(tmp_path, PortalContour.TARGET)
    old_key = Ed25519PrivateKey.generate()
    old_fingerprint = ed25519_public_key_fingerprint(old_key.public_key())

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

        prepared = source.post(
            "/api/settings/keys/signing/rotation/prepare",
            headers=headers,
        )
        assert prepared.status_code == 201
        new_fingerprint = prepared.json()["fingerprint"]

        pending_package = source.get(
            "/api/settings/keys/signing/rotation/trust-package",
            headers=headers,
        )
        assert pending_package.status_code == 200

        still_old_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-STILLOLD",
        )
        assert still_old_bundle.signing_key_fingerprint == old_fingerprint

    with TestClient(target_app) as target:
        target_admin = _login(target, "admin")
        headers = _auth(target_admin)
        assert target.post(
            "/api/settings/keys/trusted",
            json=_trusted_payload(old_key),
            headers=headers,
        ).status_code == 201
        imported = target.post(
            "/api/settings/keys/trusted/package",
            params={"confirm": True},
            content=pending_package.content,
            headers={**headers, "Content-Type": "application/gzip"},
        )
        assert imported.status_code == 201
        assert imported.json()["fingerprint"] == new_fingerprint

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
            still_old_bundle.archive_path,
            sidecar_path=still_old_bundle.sidecar_path,
        ).signing_key_fingerprint == old_fingerprint

    with TestClient(source_app) as source:
        source_admin = _login(source, "admin")
        activated = source.post(
            "/api/settings/keys/signing/rotation/activate",
            json={"expected_fingerprint": new_fingerprint},
            headers=_auth(source_admin),
        )
        assert activated.status_code == 200
        new_bundle = _build_chart_bundle(
            source_app.state.settings,
            "DELIVERY-20260914-NEWKEY1",
        )
        assert new_bundle.signing_key_fingerprint == new_fingerprint

    verifier = BundlePackageService(target_app.state.settings)
    assert verifier.verify_bundle(
        old_bundle.archive_path,
        sidecar_path=old_bundle.sidecar_path,
    ).signing_key_fingerprint == old_fingerprint
    assert verifier.verify_bundle(
        new_bundle.archive_path,
        sidecar_path=new_bundle.sidecar_path,
    ).signing_key_fingerprint == new_fingerprint


def test_target_retirement_blocks_ready_import_and_reports_historical_impact(
    tmp_path: Path,
) -> None:
    app = _build_app(tmp_path, PortalContour.TARGET)
    source_key = Ed25519PrivateKey.generate()
    fingerprint = ed25519_public_key_fingerprint(source_key.public_key())

    with TestClient(app) as client:
        admin = _login(client, "admin")
        headers = _auth(admin)
        added = client.post(
            "/api/settings/keys/trusted",
            json=_trusted_payload(source_key),
            headers=headers,
        )
        assert added.status_code == 201

        with app.state.session_factory() as session:
            completed = Operation(
                type=OperationType.IMPORT,
                status=OperationStatus.COMPLETED,
                actor_username="operator",
                bundle_signing_key_fingerprint=fingerprint,
            )
            ready = Operation(
                type=OperationType.IMPORT,
                status=OperationStatus.READY,
                actor_username="operator",
                bundle_signing_key_fingerprint=fingerprint,
            )
            verifying_unknown = Operation(
                type=OperationType.IMPORT,
                status=OperationStatus.VERIFYING,
                actor_username="operator",
                bundle_signing_key_fingerprint=None,
            )
            session.add_all([completed, ready, verifying_unknown])
            session.commit()
            ready_id = ready.id
            verifying_id = verifying_unknown.id

        impact = client.get(
            f"/api/settings/keys/trusted/{fingerprint}/impact",
            headers=headers,
        )
        assert impact.status_code == 200
        assert impact.json() == {
            "fingerprint": fingerprint,
            "enabled": True,
            "enabled_key_count": 1,
            "historical_import_count": 2,
            "blocking_operation_ids": [ready_id, verifying_id],
            "can_retire": False,
        }

        blocked_disable = client.patch(
            f"/api/settings/keys/trusted/{fingerprint}",
            json={"enabled": False, "confirm": True},
            headers=headers,
        )
        assert blocked_disable.status_code == 409
        assert blocked_disable.json()["error"]["code"] == "trusted_key_retirement_blocked"

        blocked_remove = client.delete(
            f"/api/settings/keys/trusted/{fingerprint}",
            params={"confirm": True},
            headers=headers,
        )
        assert blocked_remove.status_code == 409
        assert blocked_remove.json()["error"]["code"] == "trusted_key_retirement_blocked"

        replacement_key = Ed25519PrivateKey.generate()
        blocked_replace = client.put(
            f"/api/settings/keys/trusted/{fingerprint}",
            json={"pem": _public_pem(replacement_key), "confirm": True},
            headers=headers,
        )
        assert blocked_replace.status_code == 409
        assert blocked_replace.json()["error"]["code"] == "trusted_key_retirement_blocked"

        with app.state.session_factory() as session:
            ready = session.get(Operation, ready_id)
            verifying = session.get(Operation, verifying_id)
            assert ready is not None
            assert verifying is not None
            ready.status = OperationStatus.COMPLETED
            verifying.status = OperationStatus.REJECTED
            session.commit()

        allowed = client.patch(
            f"/api/settings/keys/trusted/{fingerprint}",
            json={"enabled": False, "confirm": True},
            headers=headers,
        )
        assert allowed.status_code == 200
        assert allowed.json()["action"] == "disabled"

        final_impact = client.get(
            f"/api/settings/keys/trusted/{fingerprint}/impact",
            headers=headers,
        )
        assert final_impact.status_code == 200
        assert final_impact.json()["blocking_operation_ids"] == []
        assert final_impact.json()["historical_import_count"] == 2
        assert final_impact.json()["can_retire"] is True


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
            json={"enabled": False, "confirm": True},
            headers=headers,
        )
        assert disabled.status_code == 200

    assert not legacy.exists()
    canonical_disabled = trust_dir / f"{fingerprint.removeprefix('sha256:')}.disabled"
    assert canonical_disabled.is_file()
    assert not list(trust_dir.glob("*.pem"))
