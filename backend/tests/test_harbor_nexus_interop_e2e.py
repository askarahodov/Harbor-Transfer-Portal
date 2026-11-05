"""Interoperability at the real Docker v2 transport boundary, in both directions.

A disposable distribution registry exercises the Harbor-shaped OCI transport;
the Harbor REST metadata and project/RBAC checks are narrowly stubbed. This is
not a claim of interoperability against a live Harbor installation.
Nexus uses the real versioned Docker v2 server on its own connector.
Two Portal apps have separate DB/storage, no network handoff or shared key.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from test_nexus_legacy_e2e import (
    REGISTRY_URL,
    make_settings,
    seeded_schema2_image,
)
from test_nexus_source_e2e import _settings as portal_settings
from test_nexus_target_e2e import TARGET_REGISTRY_URL, _app, _auth, _terminal

from app.config import PortalContour
from app.services.harbor_client import HarborArtifact, HarborClient
from app.services.harbor_destination_validator import (
    DestinationCapability,
    HarborDestinationValidator,
)
from app.services.harbor_profiles import HarborProfileService
from app.services.nexus_service import ImageReference, NexusService
from integration.registry_smoke import _write_oci_image_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1"
    or os.environ.get("HTP_HARBOR_COMPAT_E2E") != "1",
    reason="Requires disposable Nexus plus a distinct Docker v2 fixture",
)

HARBOR_DOCKER_URL = "http://127.0.0.1:5005"
SCHEMA2 = "application/vnd.docker.distribution.manifest.v2+json"


def _harbor_profile(app, settings, *, name: str) -> str:
    with app.state.session_factory() as session:
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name=name, url=HARBOR_DOCKER_URL, username=None,
            verify_tls=False, enabled=True, registry_type="harbor",
        )
        session.commit()
        return profile.id


def _harbor_fixture_image(tmp_path: Path) -> str:
    """Seed real schema2 bytes, then read actual remote registry manifest digest."""
    layout = tmp_path / "harbor-oci"
    _write_oci_image_fixture(layout)
    subprocess.run(
        [
            "skopeo", "copy", "--format", "v2s2",
            "--dest-tls-verify=false", f"oci:{layout}",
            "docker://127.0.0.1:5005/harbor-e2e/source:1.0.0",
        ],
        capture_output=True, check=True, timeout=120,
    )
    raw = subprocess.run(
        [
            "skopeo", "inspect", "--raw", "--tls-verify=false",
            "docker://127.0.0.1:5005/harbor-e2e/source:1.0.0",
        ],
        capture_output=True, check=True, timeout=60,
    ).stdout
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("direction", ["harbor-to-nexus", "nexus-to-harbor"])
def test_signed_docker_bundle_interoperability_via_separate_registry_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, direction: str,
) -> None:
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text().strip()
    harbor_digest = _harbor_fixture_image(tmp_path)
    nexus_source = portal_settings(tmp_path / "seed-nexus-settings")
    nexus_digest = seeded_schema2_image(
        NexusService(
            MagicMock(), make_settings(tmp_path / "seed-nexus", password)
        ),
        nexus_source.skopeo_payload_root / "seed-layout",
        password,
    )
    is_harbor_source = direction == "harbor-to-nexus"
    image_digest = harbor_digest if is_harbor_source else nexus_digest
    source_repo = "harbor-e2e" if is_harbor_source else "htp-e2e"
    target_project = (
        "htp-interop-nexus" if is_harbor_source else "harbor-interop-target"
    )
    target_repo = f"{target_project}/source"

    # We only simulate Harbor's REST metadata. The actual image bytes travel
    # through the production Skopeo adapter from the Docker v2 fixture.
    rest_calls: list[tuple[str, str, str]] = []

    def artifact_lookup(_self, project: str, repository: str, reference: str):
        rest_calls.append((project, repository, reference))
        assert (project, repository, reference) == ("harbor-e2e", "source", "1.0.0")
        return HarborArtifact(
            digest=harbor_digest, type="IMAGE", media_type=SCHEMA2, size=0,
        )

    monkeypatch.setattr(HarborClient, "get_artifact", artifact_lookup)

    # The fake registry lacks Harbor's management API; the narrowly stubbed
    # validator only allows the known disposable project and repository.
    allowed: list[tuple[str, str]] = []

    async def validate_fixture(_self, project: str, repository: str):
        allowed.append((project, repository))
        if (project, repository) != ("harbor-interop-target", target_repo):
            return DestinationCapability(
                False, False, "import_destination_write_forbidden",
                "Test fixture prohibits the requested destination",
            )
        return DestinationCapability(True, True)

    monkeypatch.setattr(HarborDestinationValidator, "validate", validate_fixture)

    source = portal_settings(tmp_path / "source")
    source_app, source_nexus_id = _app(source, password, url=REGISTRY_URL)
    source_profile = (
        _harbor_profile(source_app, source, name="Harbor source fixture")
        if is_harbor_source else source_nexus_id
    )
    selection = {
        "harbor_profile_id": source_profile,
        "artifacts": [{
            "kind": "container-image", "project": source_repo,
            "repository": "source", "reference": "1.0.0",
            "digest": image_digest,
        }],
    }
    with TestClient(source_app) as client:
        headers = _auth(client)
        preview = client.post("/api/exports/preview", headers=headers, json=selection)
        assert preview.status_code == 200, preview.text
        assert preview.json()["artifacts"][0]["source_digest"] == image_digest

        invalid = client.post(
            "/api/exports/preview", headers=headers,
            json={
                **selection,
                "artifacts": [{**selection["artifacts"][0], "digest": "sha256:" + "0" * 64}],
            },
        )
        assert invalid.status_code == 409, invalid.text
        assert invalid.json()["error"]["code"] == "export_source_changed"

        start = client.post("/api/exports", headers=headers, json=selection)
        assert start.status_code == 202, start.text
        export_id = start.json()["operation_id"]
        result = _terminal(client, headers, export_id)
        assert result["status"] == "COMPLETED", result
        info = client.get(f"/api/exports/{export_id}/bundle", headers=headers)
        assert info.status_code == 200, info.text
        metadata = info.json()
        ticket = client.post(
            f"/api/exports/{export_id}/download-ticket", headers=headers
        )
        assert ticket.status_code == 200, ticket.text
        downloaded = client.get(ticket.json()["download_url"])
        assert downloaded.status_code == 200, downloaded.text
        archive = downloaded.content
        assert hashlib.sha256(archive).hexdigest() == metadata["sha256"]

    # Provenance is signed inside the archive, not inferred from TARGET choice.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        manifest_file = bundle.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.load(manifest_file)
    provenance = manifest["source"]
    if is_harbor_source:
        assert provenance["harbor"] == HARBOR_DOCKER_URL
        assert provenance.get("registry_type") in (None, "harbor")
    else:
        assert provenance["registry_type"] == "nexus"
        assert provenance["registry"] == REGISTRY_URL
        assert provenance.get("harbor") is None
    assert manifest["artifacts"][0]["source_digest"] == image_digest

    filename = metadata["archive_name"]
    sidecar = (source.bundle_outgoing_root / f"{filename}.sha256").read_bytes()
    handoff = (
        source.bundle_outgoing_root
        / f"{metadata['delivery_id']}.htp-handoff.json"
    ).read_bytes()
    target_seed = portal_settings(tmp_path / "target")
    public = (source.bundle_trusted_public_keys_dir / "source.pem").read_bytes()
    (target_seed.bundle_trusted_public_keys_dir / "source.pem").write_bytes(public)
    target_seed.bundle_signing_private_key_file.unlink()
    target = target_seed.model_copy(update={
        "portal_contour": PortalContour.TARGET,
        "import_staging_root": tmp_path / "target" / "data" / "incoming" / "staged",
        "import_discovery_root": tmp_path / "target" / "data" / "incoming" / "discovery",
        "import_receipt_root": tmp_path / "target" / "data" / "receipts",
    })
    target_app, target_nexus_id = _app(
        target, password, url=TARGET_REGISTRY_URL,
    )
    target_profile = (
        target_nexus_id if is_harbor_source
        else _harbor_profile(target_app, target, name="Harbor target fixture")
    )
    handoff_headers = {
        "x-htp-bundle-filename": filename,
        "x-htp-sidecar-base64": base64.b64encode(sidecar).decode("ascii"),
        "x-htp-handoff-base64": base64.b64encode(handoff).decode("ascii"),
    }
    with TestClient(target_app) as client:
        headers = _auth(client)
        corrupt = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive[:-1] + bytes([archive[-1] ^ 1]),
            headers={**headers, **handoff_headers},
        )
        assert corrupt.status_code in {400, 409, 422}, corrupt.text
        assert not list(target.import_staging_root.rglob("*.htp.tar.gz"))

        upload = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **handoff_headers},
        )
        assert upload.status_code == 202, upload.text
        import_id = upload.json()["operation_id"]
        assert _terminal(client, headers, import_id, ready=True)["status"] == "READY"
        preview = client.get(
            f"/api/imports/{import_id}/preview", headers=headers
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["signature_verified"] is True
        assert preview.json()["checksum_verified"] is True
        assert preview.json()["artifacts"][0]["expected_digest"] == image_digest

        mapping = {
            "harbor_profile_id": target_profile,
            "container_image_project": target_project,
        }
        plan = client.put(
            f"/api/imports/{import_id}/destination-plan",
            headers=headers, json=mapping,
        )
        assert plan.status_code == 200, plan.text
        payload = plan.json()
        assert payload["valid"] is True, payload
        assert payload["artifacts"][0]["classification"] == "NEW"
        assert payload["artifacts"][0]["target_repository"] == target_repo
        execute = client.post(
            f"/api/imports/{import_id}/execute", headers=headers,
            json={"destination_plan_id": payload["plan_id"], "overwrite_conflicts": False},
        )
        assert execute.status_code == 202, execute.text
        assert _terminal(client, headers, import_id)["status"] == "COMPLETED"
        receipt = client.get(f"/api/imports/{import_id}/receipt", headers=headers)
        assert receipt.status_code == 200, receipt.text
        row = receipt.json()["artifacts"][0]
        assert row["status"] == "VERIFIED"
        assert row["expected_digest"] == image_digest
        assert row["target_digest"] == image_digest
        assert row["target_repository"] == target_repo

        replay = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **handoff_headers},
        )
        assert replay.status_code == 202, replay.text
        replay_id = replay.json()["operation_id"]
        assert _terminal(client, headers, replay_id, ready=True)["status"] == "READY"
        again = client.put(
            f"/api/imports/{replay_id}/destination-plan",
            headers=headers, json=mapping,
        )
        assert again.status_code == 200, again.text
        assert again.json()["artifacts"][0]["classification"] == "SAME"
        retry = client.post(
            f"/api/imports/{replay_id}/execute", headers=headers,
            json={"destination_plan_id": again.json()["plan_id"]},
        )
        assert retry.status_code == 202, retry.text
        assert _terminal(client, headers, replay_id)["status"] == "COMPLETED"
        replay_receipt = client.get(
            f"/api/imports/{replay_id}/receipt", headers=headers
        )
        assert replay_receipt.status_code == 200
        assert replay_receipt.json()["artifacts"][0]["status"] == "SKIPPED"
        assert replay_receipt.json()["artifacts"][0]["target_digest"] == image_digest

    assert rest_calls == (
        [("harbor-e2e", "source", "1.0.0")] * 3 if is_harbor_source else []
    )
    assert allowed if not is_harbor_source else not allowed
    if is_harbor_source:
        target_client = NexusService(
            MagicMock(),
            make_settings(
                tmp_path / "target-check", password, url=TARGET_REGISTRY_URL,
            ),
        )
        observed = asyncio.run(
            target_client.inspect_image(ImageReference(target_repo, "1.0.0"))
        )
        assert observed.digest == image_digest
    else:
        observed = subprocess.run(
            [
                "skopeo", "inspect", "--raw", "--tls-verify=false",
                f"docker://127.0.0.1:5005/{target_repo}:1.0.0",
            ],
            capture_output=True, check=True, timeout=60,
        ).stdout
        assert "sha256:" + hashlib.sha256(observed).hexdigest() == image_digest
