"""Real Nexus Docker v2 SOURCE API -> signed bundle -> ticketed download E2E.

Runs only with the disposable Nexus 3.69 OSS service in the dedicated workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_nexus_legacy_e2e import REGISTRY_URL, make_settings, seeded_schema2_image

from app.auth.security import create_export_download_token, hash_password
from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus
from app.main import create_app
from app.services.bundle_package_service import BundlePackageService
from app.services.harbor_profiles import HarborProfileService
from app.services.nexus_service import NexusService, NexusServiceError

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1",
    reason="Requires disposable Nexus 3.69 OSS and Skopeo in CI",
)


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    signing = data / "keys" / "source-private.pem"
    trusted = data / "keys" / "trusted"
    signing.parent.mkdir(parents=True)
    trusted.mkdir(parents=True)
    key = Ed25519PrivateKey.generate()
    signing.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    signing.chmod(0o600)
    (trusted / "source.pem").write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        database_url=f"sqlite:///{tmp_path / 'source.db'}",
        jwt_secret="nexus-e2e-jwt-secret-test-1234567890",
        harbor_url="https://unreachable-harbor.invalid",
        nexus_url="http://127.0.0.1:59999",
        nexus_user="admin",
        nexus_password="intentionally-wrong-global-password",
        nexus_verify_tls=False,
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        skopeo_binary="skopeo",
        skopeo_payload_root=data,
        skopeo_temp_root=data / "tmp" / "skopeo",
        helm_workspace_root=data / "packages",
        helm_temp_root=data / "tmp" / "helm",
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "incoming" / "verified",
        bundle_signing_private_key_file=signing,
        bundle_trusted_public_keys_dir=trusted,
    )


def test_real_source_api_signed_bundle_and_browser_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text(
        encoding="utf-8"
    ).strip()
    settings = _settings(tmp_path)
    # Seed via the real Nexus Docker schema2 registry before creating the app.
    seed = NexusService(MagicMock(), make_settings(tmp_path / "seed", password))
    expected = seeded_schema2_image(
        seed, settings.skopeo_payload_root / "seed", password,
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.db_engine)
    with app.state.session_factory() as session:
        actor = UserRepository(session).create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name="Live Nexus 3.69 OSS",
            url=REGISTRY_URL,
            username="admin",
            verify_tls=False,
            enabled=True,
            registry_type="nexus",
        )
        profiles.rotate_credential(profile.id, password)
        profile_id = profile.id
        actor_id = actor.id
        session.commit()

    selection = {
        "artifacts": [{
            "kind": "container-image",
            "project": "htp-e2e",
            "repository": "source",
            "reference": "1.0.0",
            "digest": expected,
        }],
        "harbor_profile_id": profile_id,
        "comment": "SOURCE API Nexus E2E",
    }
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={
            "username": "operator",
            "password": "operator-password-123",
        })
        assert login.status_code == 200, login.text
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        preview = client.post("/api/exports/preview", json=selection, headers=auth)
        assert preview.status_code == 200, preview.text
        assert preview.json()["artifacts"][0]["source_digest"] == expected
        assert preview.json()["artifacts"][0]["project"] == "htp-e2e"
        assert preview.json()["artifacts"][0]["repository"] == "source"
        assert preview.json()["artifacts"][0]["size_bytes"] is None

        # Nonexistent Nexus tag must fail closed before starting a worker or
        # creating any downloadable output.
        missing = {**selection, "artifacts": [{
            **selection["artifacts"][0], "reference": "tag-does-not-exist-433",
        }]}
        missing_preview = client.post("/api/exports/preview", json=missing, headers=auth)
        assert missing_preview.status_code in {404, 422}, missing_preview.text
        assert missing_preview.json()["error"]["code"] == "nexus_not_found"
        missing_start = client.post("/api/exports", json=missing, headers=auth)
        assert missing_start.status_code in {404, 422}, missing_start.text
        assert missing_start.json()["error"]["code"] == "nexus_not_found"
        assert not list(settings.bundle_outgoing_root.glob("*.htp.tar.gz"))

        wrong = {**selection, "artifacts": [{
            **selection["artifacts"][0], "digest": "sha256:" + "f" * 64,
        }]}
        changed = client.post("/api/exports/preview", json=wrong, headers=auth)
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "export_source_changed"

        start = client.post("/api/exports", json=selection, headers=auth)
        assert start.status_code == 202, start.text
        operation_id = start.json()["operation_id"]

        deadline = time.monotonic() + 100
        while True:
            status = client.get(f"/api/operations/{operation_id}", headers=auth)
            assert status.status_code == 200, status.text
            current = status.json()["status"]
            if current in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            assert time.monotonic() < deadline, status.text
            time.sleep(0.25)
        assert current == OperationStatus.COMPLETED.value, status.text

        bundle = client.get(f"/api/exports/{operation_id}/bundle", headers=auth)
        assert bundle.status_code == 200, bundle.text
        metadata = bundle.json()
        assert metadata["sha256"]
        assert metadata["archive_size"] > 0

        handoff = client.get(f"/api/exports/{operation_id}/handoff", headers=auth)
        assert handoff.status_code == 200, handoff.text
        assert handoff.headers["content-type"].startswith("application/json")

        no_ticket = client.get(f"/api/exports/{operation_id}/download")
        assert no_ticket.status_code == 401
        ticket = client.post(
            f"/api/exports/{operation_id}/download-ticket", headers=auth,
        )
        assert ticket.status_code == 200, ticket.text
        assert "httponly" in ticket.headers["set-cookie"].lower()
        downloaded = client.get(ticket.json()["download_url"])
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["content-type"].startswith("application/gzip")
        assert downloaded.headers["content-length"] == str(len(downloaded.content))
        digest = hashlib.sha256(downloaded.content).hexdigest()
        assert digest == metadata["sha256"]
        assert downloaded.headers["x-checksum-sha256"] == digest
        assert metadata["archive_name"] in downloaded.headers["content-disposition"]

        # Exercise real JWT expiry and operation binding, without sleeping.
        client.cookies.clear()
        expired = create_export_download_token(
            user_id=actor_id,
            operation_id=operation_id,
            secret=settings.jwt_secret.get_secret_value(),
            lifetime_seconds=-60,
        )
        denied_expired = client.get(
            f"/api/exports/{operation_id}/download",
            cookies={"htp_export_download": expired},
        )
        assert denied_expired.status_code == 401, denied_expired.text
        assert denied_expired.json()["error"]["code"] == "download_auth_invalid"
        wrong_operation = create_export_download_token(
            user_id=actor_id,
            operation_id=operation_id + 1,
            secret=settings.jwt_secret.get_secret_value(),
            lifetime_seconds=60,
        )
        denied_binding = client.get(
            f"/api/exports/{operation_id}/download",
            cookies={"htp_export_download": wrong_operation},
        )
        assert denied_binding.status_code == 401, denied_binding.text
        assert denied_binding.json()["error"]["code"] == "download_auth_invalid"

        # Force the real SOURCE operation worker to fail during Nexus copy,
        # after a successful preview; it must never expose a partial delivery.
        async def fail_copy(_service, _image, _destination):  # type: ignore[no-untyped-def]
            raise NexusServiceError(
                "nexus_registry_connection_failed",
                "Nexus connection unavailable during materialization",
            )

        monkeypatch.setattr(NexusService, "export_image", fail_copy)
        failed_start = client.post("/api/exports", json=selection, headers=auth)
        assert failed_start.status_code == 202, failed_start.text
        failed_id = failed_start.json()["operation_id"]
        deadline = time.monotonic() + 60
        while True:
            failed_status = client.get(f"/api/operations/{failed_id}", headers=auth)
            assert failed_status.status_code == 200, failed_status.text
            failure = failed_status.json()
            if failure["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            assert time.monotonic() < deadline, failure
            time.sleep(0.25)
        assert failure["status"] == "FAILED", failure
        assert failure["error_code"] == "nexus_registry_connection_failed"
        assert failure["bundle"] is None
        assert all(item["status"] == "FAILED" for item in failure["artifacts"])
        assert all(
            item["error_code"] == "nexus_registry_connection_failed"
            for item in failure["artifacts"]
        )
        failed_delivery = failed_start.json()["delivery_id"]
        assert not list(settings.bundle_outgoing_root.glob(f"{failed_delivery}*"))
        assert client.get(
            f"/api/exports/{failed_id}/bundle", headers=auth
        ).status_code == 409
        assert client.post(
            f"/api/exports/{failed_id}/download-ticket", headers=auth
        ).status_code == 409

    archive = settings.bundle_outgoing_root / metadata["archive_name"]
    assert archive.read_bytes() == downloaded.content
    verified = BundlePackageService(settings).verify_bundle(
        archive, sidecar_path=archive.with_name(archive.name + ".sha256"),
    )
    assert verified.manifest.schema_version == "1.1"
    assert verified.manifest.source.registry_type == "nexus"
    assert verified.manifest.source.registry == REGISTRY_URL
    assert verified.manifest.source.harbor is None
    assert verified.manifest.artifacts[0].source_digest == expected
    # Guard the exact Docker namespace after preview -> materialization -> signing.
    assert verified.manifest.artifacts[0].repository == "htp-e2e/source"
    with tarfile.open(archive, "r:gz") as packed:
        manifest_file = packed.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.load(manifest_file)
    assert "harbor" not in manifest["source"]
    assert manifest["source"]["registry_type"] == "nexus"
    assert actor_id > 0
