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
from urllib.parse import urlsplit

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
    or (
        os.environ.get("HTP_HARBOR_COMPAT_E2E") != "1"
        and os.environ.get("HTP_REAL_HARBOR_E2E") != "1"
    ),
    reason="Requires disposable Nexus and Harbor-shaped or real Harbor transport",
)

REAL_HARBOR = os.environ.get("HTP_REAL_HARBOR_E2E") == "1"
HARBOR_DOCKER_URL = os.environ.get(
    "HTP_REAL_HARBOR_URL", "http://127.0.0.1:5005"
)
SCHEMA2 = "application/vnd.docker.distribution.manifest.v2+json"


def _robot_credentials(*, source: bool) -> tuple[str, str]:
    filename = (
        "HTP_HARBOR_SOURCE_ROBOT_FILE" if source else "HTP_HARBOR_TARGET_ROBOT_FILE"
    )
    payload = json.loads(Path(os.environ[filename]).read_text(encoding="utf-8"))
    return payload["username"], payload["password"]


def _harbor_profile(app, settings, *, name: str) -> str:
    username, password = (
        _robot_credentials(source="source" in name.lower())
        if REAL_HARBOR else (None, None)
    )
    with app.state.session_factory() as session:
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name=name, url=HARBOR_DOCKER_URL, username=username,
            verify_tls=REAL_HARBOR, enabled=True, registry_type="harbor",
        )
        if REAL_HARBOR:
            assert password is not None
            profiles.rotate_credential(profile.id, password)
            ca = Path(os.environ["HTP_REAL_HARBOR_CA_FILE"]).read_text(encoding="utf-8")
            profiles.install_ca(profile.id, ca)
        session.commit()
        return profile.id


def _harbor_cli_flags(tmp_path: Path, *, destination: bool) -> list[str]:
    if not REAL_HARBOR:
        return ["--dest-tls-verify=false"] if destination else ["--tls-verify=false"]
    host = urlsplit(HARBOR_DOCKER_URL).netloc
    secret = Path(os.environ["HTP_HARBOR_ADMIN_PASSWORD_FILE"]).read_text().strip()
    auth_file = tmp_path / "harbor-admin-auth.json"
    auth_file.write_text(json.dumps({
        "auths": {host: {
            "auth": base64.b64encode(f"admin:{secret}".encode()).decode("ascii")
        }}
    }), encoding="utf-8")
    auth_file.chmod(0o600)
    cert_dir = os.environ["HTP_REAL_HARBOR_SKOPEO_CERT_DIR"]
    return (
        ["--dest-authfile", str(auth_file), "--dest-cert-dir", cert_dir]
        if destination
        else ["--authfile", str(auth_file), "--cert-dir", cert_dir]
    )


def _harbor_fixture_image(tmp_path: Path) -> str:
    """Seed real schema2 bytes, then read actual remote registry manifest digest."""
    layout = tmp_path / "harbor-oci"
    _write_oci_image_fixture(layout)
    subprocess.run(
        [
            "skopeo", "copy", "--format", "v2s2",
            *_harbor_cli_flags(tmp_path, destination=True),
            f"oci:{layout}",
            f"docker://{urlsplit(HARBOR_DOCKER_URL).netloc}/harbor-e2e/source:1.0.0",
        ],
        capture_output=True, check=True, timeout=120,
    )
    raw = subprocess.run(
        [
            "skopeo", "inspect", "--raw",
            *_harbor_cli_flags(tmp_path, destination=False),
            f"docker://{urlsplit(HARBOR_DOCKER_URL).netloc}/harbor-e2e/source:1.0.0",
        ],
        capture_output=True, check=True, timeout=60,
    ).stdout
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _overwrite_disposable_harbor_tag_with_conflict(layout: Path) -> str:
    """Replace test-only Harbor-shaped registry tag with a new Docker v2 digest."""
    _write_oci_image_fixture(layout)
    index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    manifest_entry = index["manifests"][0]
    root = layout / "blobs" / "sha256"
    manifest_path = root / manifest_entry["digest"].removeprefix("sha256:")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    configuration = root / manifest["config"]["digest"].removeprefix("sha256:")
    changed = json.loads(configuration.read_text(encoding="utf-8"))
    changed["created"] = "2026-11-05T19:00:00Z"

    def save_blob(document: dict) -> tuple[str, int]:
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        (root / digest).write_bytes(data)
        return f"sha256:{digest}", len(data)

    config_digest, config_size = save_blob(changed)
    manifest["config"]["digest"] = config_digest
    manifest["config"]["size"] = config_size
    manifest_digest, manifest_size = save_blob(manifest)
    manifest_entry["digest"] = manifest_digest
    manifest_entry["size"] = manifest_size
    (layout / "index.json").write_text(json.dumps(index), encoding="utf-8")
    subprocess.run(
        [
            "skopeo", "copy", "--format", "v2s2",
            *_harbor_cli_flags(layout.parent, destination=True),
            f"oci:{layout}",
            f"docker://{urlsplit(HARBOR_DOCKER_URL).netloc}"
            "/harbor-interop-target/source:1.0.0",
        ],
        capture_output=True, check=True, timeout=120,
    )
    raw = subprocess.run(
        [
            "skopeo", "inspect", "--raw",
            *_harbor_cli_flags(layout.parent, destination=False),
            f"docker://{urlsplit(HARBOR_DOCKER_URL).netloc}"
            "/harbor-interop-target/source:1.0.0",
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

    if not REAL_HARBOR:
        monkeypatch.setattr(HarborClient, "get_artifact", artifact_lookup)
    else:
        username, robot_secret = _robot_credentials(source=True)
        with HarborClient(
            base_url=HARBOR_DOCKER_URL,
            username=username,
            password=robot_secret,
            verify=os.environ["HTP_REAL_HARBOR_CA_FILE"],
        ) as harbor:
            observed = harbor.get_artifact("harbor-e2e", "source", "1.0.0")
            assert observed.digest == harbor_digest

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

    if not REAL_HARBOR:
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
            "kind": "container-image",
            "project": source_repo if is_harbor_source else None,
            "repository": "source" if is_harbor_source else f"{source_repo}/source",
            "reference": "1.0.0",
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
    # A SOURCE-only Harbor robot must not gain TARGET push rights merely
    # because its credentials are accepted by the Harbor Core.
    denied_profile = (
        _harbor_profile(target_app, target, name="Harbor source denied fixture")
        if REAL_HARBOR and not is_harbor_source else None
    )
    handoff_headers = {
        "x-htp-bundle-filename": filename,
        "x-htp-sidecar-base64": base64.b64encode(sidecar).decode("ascii"),
        "x-htp-handoff-base64": base64.b64encode(handoff).decode("ascii"),
    }
    competing_digest: str | None = None
    with TestClient(target_app) as client:
        headers = _auth(client)
        corrupt = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive[:-1] + bytes([archive[-1] ^ 1]),
            headers={**headers, **handoff_headers},
        )
        assert corrupt.status_code in {400, 409, 422}, corrupt.text
        assert not list(target.import_staging_root.rglob("*.htp.tar.gz"))

        if denied_profile is not None:
            denied_upload = client.post(
                f"/api/imports/upload?profile_id={denied_profile}",
                content=archive, headers={**headers, **handoff_headers},
            )
            assert denied_upload.status_code == 202, denied_upload.text
            denied_id = denied_upload.json()["operation_id"]
            assert _terminal(client, headers, denied_id, ready=True)["status"] == "READY"
            # Real Harbor project-list and scoped robot identity are used here;
            # no mocked Harbor REST, validator or token authorization.
            denied_plan = client.put(
                f"/api/imports/{denied_id}/destination-plan",
                headers=headers, json={
                    "harbor_profile_id": denied_profile,
                    "container_image_project": "harbor-interop-target",
                },
            )
            assert denied_plan.status_code == 200, denied_plan.text
            denied_data = denied_plan.json()
            assert denied_data["valid"] is False, denied_data
            assert denied_data["artifacts"][0]["write_allowed"] is False
            assert denied_data["artifacts"][0]["error_code"] in {
                "import_destination_project_missing",
                "import_destination_write_forbidden",
                "import_destination_validation_failed",
            }
            refused = client.post(
                f"/api/imports/{denied_id}/execute",
                headers=headers, json={
                    "destination_plan_id": denied_data["plan_id"],
                    "overwrite_conflicts": False,
                },
            )
            assert refused.status_code in {409, 422}, refused.text

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

        if not is_harbor_source:
            # Harbor TARGET fixture conflict must never silently overwrite
            # the existing Docker tag, even after a valid signed handoff.
            competing_digest = _overwrite_disposable_harbor_tag_with_conflict(
                tmp_path / "harbor-conflict-layout",
            )
            assert competing_digest != image_digest
            third = client.post(
                f"/api/imports/upload?profile_id={target_profile}",
                content=archive, headers={**headers, **handoff_headers},
            )
            assert third.status_code == 202, third.text
            conflict_id = third.json()["operation_id"]
            assert _terminal(client, headers, conflict_id, ready=True)["status"] == "READY"
            conflict_plan = client.put(
                f"/api/imports/{conflict_id}/destination-plan",
                headers=headers, json=mapping,
            )
            assert conflict_plan.status_code == 200, conflict_plan.text
            assert conflict_plan.json()["artifacts"][0]["classification"] == "CONFLICT"
            denied = client.post(
                f"/api/imports/{conflict_id}/execute", headers=headers,
                json={"destination_plan_id": conflict_plan.json()["plan_id"]},
            )
            assert denied.status_code == 409, denied.text
            assert denied.json()["error"]["code"] == "import_conflict_blocked"
            skipped = client.post(
                f"/api/imports/{conflict_id}/execute", headers=headers,
                json={
                    "destination_plan_id": conflict_plan.json()["plan_id"],
                    "skip_conflicts": True, "overwrite_conflicts": False,
                },
            )
            assert skipped.status_code == 202, skipped.text
            assert _terminal(client, headers, conflict_id)["status"] == "COMPLETED"
            skipped_receipt = client.get(
                f"/api/imports/{conflict_id}/receipt", headers=headers
            )
            assert skipped_receipt.status_code == 200, skipped_receipt.text
            assert skipped_receipt.json()["artifacts"][0]["status"] == "SKIPPED"
            assert (
                skipped_receipt.json()["artifacts"][0]["target_digest"]
                == competing_digest
            )

    if not REAL_HARBOR:
        assert rest_calls == (
            [("harbor-e2e", "source", "1.0.0")] * 3 if is_harbor_source else []
        )
        assert allowed if not is_harbor_source else not allowed
    else:
        assert not rest_calls
        assert not allowed
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
                "skopeo", "inspect", "--raw",
                *_harbor_cli_flags(tmp_path, destination=False),
                f"docker://{urlsplit(HARBOR_DOCKER_URL).netloc}/{target_repo}:1.0.0",
            ],
            capture_output=True, check=True, timeout=60,
        ).stdout
        assert "sha256:" + hashlib.sha256(observed).hexdigest() == competing_digest
