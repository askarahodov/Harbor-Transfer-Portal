"""Opt-in real Nexus 3.69 OSS SOURCE -> signed physical handoff -> TARGET API E2E.

Both Portal instances are isolated (separate DB/storage/runtime), with no Portal-to-Portal
calls. One disposable Docker hosted connector is used as two distinct test namespaces;
this does not prove corporate network isolation or TARGET RBAC.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from test_nexus_legacy_e2e import REGISTRY_URL, make_settings, seeded_schema2_image
from test_nexus_source_e2e import _settings as source_settings

from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.main import create_app
from app.services.harbor_profiles import HarborProfileService
from app.services.nexus_service import ImageReference, NexusService, TargetState

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1",
    reason="Requires disposable Nexus 3.69 OSS and Skopeo",
)


def _app(settings: Settings, password: str):
    app = create_app(settings)
    Base.metadata.create_all(app.state.db_engine)
    with app.state.session_factory() as session:
        UserRepository(session).create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name="Nexus Docker v2",
            url=REGISTRY_URL,
            username="admin",
            verify_tls=False,
            enabled=True,
            registry_type="nexus",
        )
        profiles.rotate_credential(profile.id, password)
        session.commit()
        profile_id = profile.id
    return app, profile_id


def _auth(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-password-123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _terminal(
    client: TestClient, headers: dict[str, str], operation_id: int, *, ready: bool = False
) -> dict:
    deadline = time.monotonic() + 120
    while True:
        response = client.get(f"/api/operations/{operation_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        terminal = ({"READY"} if ready else {"COMPLETED"}) | {
            "REJECTED",
            "FAILED",
            "CANCELLED",
        }
        if body["status"] in terminal:
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.25)


def test_nexus_signed_source_to_target_api_import_and_idempotent_replay(tmp_path: Path) -> None:
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text(
        encoding="utf-8"
    ).strip()
    source = source_settings(tmp_path / "source")
    seed = NexusService(MagicMock(), make_settings(tmp_path / "seed", password))
    digest = seeded_schema2_image(seed, source.skopeo_payload_root / "seed", password)
    source_app, source_profile = _app(source, password)

    selection = {
        "artifacts": [{
            "kind": "container-image",
            "project": "htp-e2e",
            "repository": "source",
            "reference": "1.0.0",
            "digest": digest,
        }],
        "harbor_profile_id": source_profile,
    }
    with TestClient(source_app) as source_client:
        headers = _auth(source_client)
        preview = source_client.post("/api/exports/preview", json=selection, headers=headers)
        assert preview.status_code == 200, preview.text
        result = source_client.post("/api/exports", json=selection, headers=headers)
        assert result.status_code == 202, result.text
        operation_id = result.json()["operation_id"]
        exported = _terminal(source_client, headers, operation_id)
        assert exported["status"] == "COMPLETED", exported
        info = source_client.get(f"/api/exports/{operation_id}/bundle", headers=headers)
        assert info.status_code == 200, info.text
        metadata = info.json()
        ticket = source_client.post(
            f"/api/exports/{operation_id}/download-ticket", headers=headers,
        )
        assert ticket.status_code == 200, ticket.text
        bundle = source_client.get(ticket.json()["download_url"])
        assert bundle.status_code == 200, bundle.text
        archive = bundle.content
        assert hashlib.sha256(archive).hexdigest() == metadata["sha256"]

    filename = metadata["archive_name"]
    sidecar = (source.bundle_outgoing_root / f"{filename}.sha256").read_bytes()
    handoff_path = source.bundle_outgoing_root / f"{metadata['delivery_id']}.htp-handoff.json"
    handoff = handoff_path.read_bytes()
    target_seed = source_settings(tmp_path / "target")
    # TARGET trusts only SOURCE's public key; no SOURCE private key is needed.
    public_key = (source.bundle_trusted_public_keys_dir / "source.pem").read_bytes()
    (target_seed.bundle_trusted_public_keys_dir / "source.pem").write_bytes(public_key)
    target_seed.bundle_signing_private_key_file.unlink()
    target = target_seed.model_copy(update={
        "portal_contour": PortalContour.TARGET,
        "bundle_signing_private_key_file": None,
        "import_staging_root": tmp_path / "target" / "data" / "incoming" / "staged",
        "import_discovery_root": tmp_path / "target" / "data" / "incoming" / "discovery",
        "import_receipt_root": tmp_path / "target" / "data" / "receipts",
    })
    target_app, target_profile = _app(target, password)
    browser_headers = {
        "x-htp-bundle-filename": filename,
        "x-htp-sidecar-base64": base64.b64encode(sidecar).decode("ascii"),
        "x-htp-handoff-base64": base64.b64encode(handoff).decode("ascii"),
    }
    with TestClient(target_app) as target_client:
        headers = _auth(target_client)
        upload = target_client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive,
            headers={**headers, **browser_headers},
        )
        assert upload.status_code == 202, upload.text
        operation_id = upload.json()["operation_id"]
        inspected = _terminal(target_client, headers, operation_id, ready=True)
        assert inspected["status"] == "READY", inspected
        verified = target_client.get(
            f"/api/imports/{operation_id}/preview", headers=headers,
        )
        assert verified.status_code == 200, verified.text
        body = verified.json()
        assert body["signature_verified"] is True
        assert body["checksum_verified"] is True
        assert body["artifacts"][0]["expected_digest"] == digest

        # Maps the original htp-e2e/source to a fresh, dedicated target namespace.
        plan = target_client.put(
            f"/api/imports/{operation_id}/destination-plan",
            headers=headers,
            json={
                "harbor_profile_id": target_profile,
                "container_image_project": "htp-e2e-target",
            },
        )
        assert plan.status_code == 200, plan.text
        planned = plan.json()
        assert planned["valid"] is True, planned
        assert planned["artifacts"][0]["target_repository"] == "htp-e2e-target/source"
        assert planned["artifacts"][0]["classification"] == "NEW"
        execute = target_client.post(
            f"/api/imports/{operation_id}/execute",
            headers=headers,
            json={
                "destination_plan_id": planned["plan_id"],
                "overwrite_conflicts": False,
                "skip_conflicts": False,
            },
        )
        assert execute.status_code == 202, execute.text
        imported = _terminal(target_client, headers, operation_id)
        assert imported["status"] == "COMPLETED", imported
        receipt = target_client.get(
            f"/api/imports/{operation_id}/receipt", headers=headers,
        )
        assert receipt.status_code == 200, receipt.text
        report = receipt.json()
        assert report["result"] == "COMPLETED"
        assert report["artifacts"][0]["target_repository"] == "htp-e2e-target/source"
        assert report["artifacts"][0]["expected_digest"] == digest
        assert report["artifacts"][0]["target_digest"] == digest
        assert report["artifacts"][0]["status"] == "VERIFIED"
        assert (target.import_receipt_root / f"import-{operation_id}.json").is_file()

        # A replay must be idempotent and avoid mutating a matching TARGET image.
        again = target_client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **browser_headers},
        )
        assert again.status_code == 202, again.text
        again_id = again.json()["operation_id"]
        assert _terminal(target_client, headers, again_id, ready=True)["status"] == "READY"
        replay = target_client.put(
            f"/api/imports/{again_id}/destination-plan",
            headers=headers,
            json={
                "harbor_profile_id": target_profile,
                "container_image_project": "htp-e2e-target",
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["artifacts"][0]["classification"] == "SAME"
        repeat = target_client.post(
            f"/api/imports/{again_id}/execute",
            headers=headers,
            json={"destination_plan_id": replay.json()["plan_id"]},
        )
        assert repeat.status_code == 202, repeat.text
        assert _terminal(target_client, headers, again_id)["status"] == "COMPLETED"
        receipt2 = target_client.get(f"/api/imports/{again_id}/receipt", headers=headers)
        assert receipt2.status_code == 200
        assert receipt2.json()["artifacts"][0]["status"] == "SKIPPED"
        assert receipt2.json()["artifacts"][0]["target_digest"] == digest

    checked = asyncio.run(
        seed.inspect_target(
            ImageReference("htp-e2e-target/source", "1.0.0"),
            expected_digest=digest,
        )
    )
    assert checked.state == TargetState.SAME_DIGEST
