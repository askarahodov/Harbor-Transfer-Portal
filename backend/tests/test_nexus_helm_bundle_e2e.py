"""Real Nexus classic Helm chart via signed SOURCE bundle, browser handoff and TARGET receipt.

This is an opt-in disposable integration test. Two independent Portal instances
share no database or signing private key; Nexus hosts separate Helm repositories.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from test_nexus_helm_http import chart_bytes
from test_nexus_legacy_e2e import make_settings, seeded_schema2_image
from test_nexus_source_e2e import _settings as source_settings
from test_nexus_target_e2e import TARGET_REGISTRY_URL, _app, _auth, _terminal

from app.config import PortalContour
from app.services.nexus_helm_http import NexusHelmHttpService
from app.services.nexus_service import ImageReference, NexusService

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1",
    reason="Requires disposable Nexus classic Helm hosted repositories",
)


def _helm_service(password: str) -> NexusHelmHttpService:
    service = NexusHelmHttpService.__new__(NexusHelmHttpService)
    service.base_url = os.environ["HTP_NEXUS_HELM_HTTPS_URL"]
    service.username = "admin"
    service.password = password
    service.verify = os.environ["HTP_NEXUS_TLS_CA_FILE"]
    import httpx

    service.timeout = httpx.Timeout(25)
    return service


def test_real_helm_signed_browser_handoff_to_target_receipt(tmp_path: Path) -> None:
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text().strip()
    source_http = _helm_service(password)
    chart = tmp_path / "htp-bundle-2.1.0.tgz"
    chart.write_bytes(chart_bytes("htp-bundle", "2.1.0"))
    digest = "sha256:" + hashlib.sha256(chart.read_bytes()).hexdigest()
    assert asyncio.run(source_http.upload_chart(
        "htp-helm-source", "htp-bundle", "2.1.0", chart
    )) == digest.removeprefix("sha256:")

    source = source_settings(tmp_path / "source")
    source_app, source_profile = _app(
        source, password, search_url=os.environ["HTP_NEXUS_HELM_HTTPS_URL"]
    )
    selection = {
        "harbor_profile_id": source_profile,
        "artifacts": [{
            "kind": "helm-chart",
            "project": "htp-helm-source",
            "repository": "htp-bundle",
            "reference": "2.1.0",
            "digest": digest,
        }],
    }
    with TestClient(source_app) as client:
        headers = _auth(client)
        preview = client.post("/api/exports/preview", json=selection, headers=headers)
        assert preview.status_code == 200, preview.text
        assert preview.json()["artifacts"][0]["source_digest"] == digest
        response = client.post("/api/exports", json=selection, headers=headers)
        assert response.status_code == 202, response.text
        export_id = response.json()["operation_id"]
        completed = _terminal(client, headers, export_id)
        assert completed["status"] == "COMPLETED", completed
        bundle_response = client.get(f"/api/exports/{export_id}/bundle", headers=headers)
        assert bundle_response.status_code == 200, bundle_response.text
        metadata = bundle_response.json()
        ticket = client.post(
            f"/api/exports/{export_id}/download-ticket", headers=headers
        )
        assert ticket.status_code == 200, ticket.text
        browser_download = client.get(ticket.json()["download_url"])
        assert browser_download.status_code == 200, browser_download.text
        archive = browser_download.content
        assert hashlib.sha256(archive).hexdigest() == metadata["sha256"]

    filename = metadata["archive_name"]
    sidecar = (source.bundle_outgoing_root / f"{filename}.sha256").read_bytes()
    handoff = (
        source.bundle_outgoing_root
        / f"{metadata['delivery_id']}.htp-handoff.json"
    ).read_bytes()
    target_seed = source_settings(tmp_path / "target")
    public = (source.bundle_trusted_public_keys_dir / "source.pem").read_bytes()
    (target_seed.bundle_trusted_public_keys_dir / "source.pem").write_bytes(public)
    target_seed.bundle_signing_private_key_file.unlink()
    target = target_seed.model_copy(update={
        "portal_contour": PortalContour.TARGET,
        "import_staging_root": tmp_path / "target" / "data" / "incoming" / "staged",
        "import_discovery_root": tmp_path / "target" / "data" / "incoming" / "discovery",
        "import_receipt_root": tmp_path / "target" / "data" / "receipts",
    })
    target_app, target_profile = _app(
        target, password, search_url=os.environ["HTP_NEXUS_HELM_HTTPS_URL"]
    )
    handoff_headers = {
        "x-htp-bundle-filename": filename,
        "x-htp-sidecar-base64": base64.b64encode(sidecar).decode("ascii"),
        "x-htp-handoff-base64": base64.b64encode(handoff).decode("ascii"),
    }

    with TestClient(target_app) as client:
        headers = _auth(client)
        # Reject tampered physical media before importing anything.
        tampered = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive[:-1] + bytes([archive[-1] ^ 1]),
            headers={**headers, **handoff_headers},
        )
        assert tampered.status_code in {400, 409, 422}, tampered.text

        uploaded = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **handoff_headers},
        )
        assert uploaded.status_code == 202, uploaded.text
        operation_id = uploaded.json()["operation_id"]
        ready = _terminal(client, headers, operation_id, ready=True)
        assert ready["status"] == "READY", ready
        verified = client.get(
            f"/api/imports/{operation_id}/preview", headers=headers
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["signature_verified"] is True
        assert verified.json()["checksum_verified"] is True
        assert verified.json()["artifacts"][0]["expected_digest"] == digest

        plan = client.put(
            f"/api/imports/{operation_id}/destination-plan", headers=headers,
            json={
                "harbor_profile_id": target_profile,
                "helm_chart_project": "htp-helm-target",
            },
        )
        assert plan.status_code == 200, plan.text
        planned = plan.json()
        assert planned["valid"] is True, planned
        assert planned["artifacts"][0]["target_repository"] == "htp-helm-target"
        assert planned["artifacts"][0]["classification"] == "NEW"
        assert planned["artifacts"][0]["write_allowed"] is True
        assert planned["artifacts"][0]["write_permission_verified"] is False
        execute = client.post(
            f"/api/imports/{operation_id}/execute", headers=headers,
            json={"destination_plan_id": planned["plan_id"],
                  "overwrite_conflicts": False},
        )
        assert execute.status_code == 202, execute.text
        imported = _terminal(client, headers, operation_id)
        assert imported["status"] == "COMPLETED", imported
        receipt = client.get(
            f"/api/imports/{operation_id}/receipt", headers=headers
        )
        assert receipt.status_code == 200, receipt.text
        item = receipt.json()["artifacts"][0]
        assert item["artifact_type"] == "helm-chart"
        assert item["target_repository"] == "htp-helm-target"
        assert item["expected_digest"] == digest
        assert item["target_digest"] == digest
        assert item["status"] == "VERIFIED"
        assert (target.import_receipt_root / f"import-{operation_id}.json").is_file()

        again = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **handoff_headers},
        )
        assert again.status_code == 202, again.text
        replay_id = again.json()["operation_id"]
        assert _terminal(client, headers, replay_id, ready=True)["status"] == "READY"
        replay = client.put(
            f"/api/imports/{replay_id}/destination-plan", headers=headers,
            json={
                "harbor_profile_id": target_profile,
                "helm_chart_project": "htp-helm-target",
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["artifacts"][0]["classification"] == "SAME"
        retry = client.post(
            f"/api/imports/{replay_id}/execute", headers=headers,
            json={"destination_plan_id": replay.json()["plan_id"]},
        )
        assert retry.status_code == 202, retry.text
        assert _terminal(client, headers, replay_id)["status"] == "COMPLETED"
        receipt2 = client.get(
            f"/api/imports/{replay_id}/receipt", headers=headers
        )
        assert receipt2.status_code == 200, receipt2.text
        assert receipt2.json()["artifacts"][0]["status"] == "SKIPPED"
        assert receipt2.json()["artifacts"][0]["target_digest"] == digest

    published = asyncio.run(
        _helm_service(password).list_charts("htp-helm-target")
    )
    assert published.available
    assert any(
        item.name == "htp-bundle"
        and item.version == "2.1.0"
        and "sha256:" + item.digest == digest
        for item in published.items
    )



def test_real_mixed_docker_and_classic_helm_signed_bundle_to_target(
    tmp_path: Path,
) -> None:
    """One signed bundle must move two different Nexus protocols without OCI.

    Distinct Portal DBs, private/source signing key only, browser download,
    one TARGET plan with two repository mappings, receipt, and replay.
    """
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text().strip()
    source = source_settings(tmp_path / "source-mixed")
    image_digest = seeded_schema2_image(
        NexusService(MagicMock(), make_settings(tmp_path / "seed-mixed", password)),
        source.skopeo_payload_root / "seed-mixed",
        password,
    )
    helm_source = _helm_service(password)
    chart = tmp_path / "htp-mixed-7.1.0.tgz"
    chart.write_bytes(chart_bytes("htp-mixed", "7.1.0"))
    chart_digest = "sha256:" + hashlib.sha256(chart.read_bytes()).hexdigest()
    assert asyncio.run(helm_source.upload_chart(
        "htp-helm-source", "htp-mixed", "7.1.0", chart
    )) == chart_digest.removeprefix("sha256:")

    source_app, source_profile = _app(
        source, password, search_url=os.environ["HTP_NEXUS_HELM_HTTPS_URL"]
    )
    selection = {
        "harbor_profile_id": source_profile,
        "artifacts": [
            {
                "kind": "container-image", "project": None,
                "repository": "htp-e2e/source", "reference": "1.0.0",
                "digest": image_digest,
            },
            {
                "kind": "helm-chart", "project": "htp-helm-source",
                "repository": "htp-mixed", "reference": "7.1.0",
                "digest": chart_digest,
            },
        ],
    }
    expected = {"container-image": image_digest, "helm-chart": chart_digest}
    with TestClient(source_app) as client:
        headers = _auth(client)
        preview = client.post("/api/exports/preview", json=selection, headers=headers)
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["artifacts"]) == 2
        assert {row["kind"]: row["source_digest"] for row in preview.json()["artifacts"]} == (
            expected
        )
        started = client.post("/api/exports", json=selection, headers=headers)
        assert started.status_code == 202, started.text
        export_id = started.json()["operation_id"]
        assert _terminal(client, headers, export_id)["status"] == "COMPLETED"
        bundle = client.get(f"/api/exports/{export_id}/bundle", headers=headers)
        assert bundle.status_code == 200, bundle.text
        metadata = bundle.json()
        ticket = client.post(
            f"/api/exports/{export_id}/download-ticket", headers=headers
        )
        assert ticket.status_code == 200, ticket.text
        download = client.get(ticket.json()["download_url"])
        assert download.status_code == 200, download.text
        archive = download.content
        assert hashlib.sha256(archive).hexdigest() == metadata["sha256"]

    filename = metadata["archive_name"]
    sidecar = (source.bundle_outgoing_root / f"{filename}.sha256").read_bytes()
    handoff = (
        source.bundle_outgoing_root
        / f"{metadata['delivery_id']}.htp-handoff.json"
    ).read_bytes()
    target_seed = source_settings(tmp_path / "target-mixed")
    public = (source.bundle_trusted_public_keys_dir / "source.pem").read_bytes()
    (target_seed.bundle_trusted_public_keys_dir / "source.pem").write_bytes(public)
    target_seed.bundle_signing_private_key_file.unlink()
    target = target_seed.model_copy(update={
        "portal_contour": PortalContour.TARGET,
        "import_staging_root": tmp_path / "target-mixed" / "data" / "incoming" / "staged",
        "import_discovery_root": tmp_path / "target-mixed" / "data" / "incoming" / "discovery",
        "import_receipt_root": tmp_path / "target-mixed" / "data" / "receipts",
    })
    target_app, target_profile = _app(
        target, password, url=TARGET_REGISTRY_URL,
        search_url=os.environ["HTP_NEXUS_HELM_HTTPS_URL"],
    )
    handoff_headers = {
        "x-htp-bundle-filename": filename,
        "x-htp-sidecar-base64": base64.b64encode(sidecar).decode("ascii"),
        "x-htp-handoff-base64": base64.b64encode(handoff).decode("ascii"),
    }
    with TestClient(target_app) as client:
        headers = _auth(client)
        bad = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive[:-1] + bytes([archive[-1] ^ 1]),
            headers={**headers, **handoff_headers},
        )
        assert bad.status_code in {400, 409, 422}, bad.text
        response = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **handoff_headers},
        )
        assert response.status_code == 202, response.text
        operation_id = response.json()["operation_id"]
        assert _terminal(client, headers, operation_id, ready=True)["status"] == "READY"
        preview = client.get(
            f"/api/imports/{operation_id}/preview", headers=headers
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["signature_verified"] is True
        assert preview.json()["checksum_verified"] is True
        assert len(preview.json()["artifacts"]) == 2

        mapping = {
            "harbor_profile_id": target_profile,
            "container_image_project": "htp-e2e-mixed",
            "helm_chart_project": "htp-helm-target",
        }
        plan = client.put(
            f"/api/imports/{operation_id}/destination-plan",
            headers=headers, json=mapping,
        )
        assert plan.status_code == 200, plan.text
        planned = plan.json()
        assert planned["valid"] is True, planned
        by_type = {row["artifact_type"]: row for row in planned["artifacts"]}
        assert set(by_type) == set(expected)
        assert by_type["container-image"]["target_repository"] == "htp-e2e-mixed/source"
        assert by_type["helm-chart"]["target_repository"] == "htp-helm-target"
        assert all(row["classification"] == "NEW" for row in by_type.values())

        run = client.post(
            f"/api/imports/{operation_id}/execute",
            headers=headers,
            json={"destination_plan_id": planned["plan_id"],
                  "overwrite_conflicts": False, "skip_conflicts": False},
        )
        assert run.status_code == 202, run.text
        assert _terminal(client, headers, operation_id)["status"] == "COMPLETED"
        receipt = client.get(
            f"/api/imports/{operation_id}/receipt", headers=headers
        )
        assert receipt.status_code == 200, receipt.text
        report = receipt.json()
        assert report["result"] == "COMPLETED"
        rows = {item["artifact_type"]: item for item in report["artifacts"]}
        assert set(rows) == set(expected)
        for kind, digest in expected.items():
            assert rows[kind]["expected_digest"] == digest
            assert rows[kind]["target_digest"] == digest
            assert rows[kind]["status"] == "VERIFIED"
        assert (target.import_receipt_root / f"import-{operation_id}.json").is_file()

        replay = client.post(
            f"/api/imports/upload?profile_id={target_profile}",
            content=archive, headers={**headers, **handoff_headers},
        )
        assert replay.status_code == 202, replay.text
        replay_id = replay.json()["operation_id"]
        assert _terminal(client, headers, replay_id, ready=True)["status"] == "READY"
        replan = client.put(
            f"/api/imports/{replay_id}/destination-plan",
            headers=headers, json=mapping,
        )
        assert replan.status_code == 200, replan.text
        assert {row["artifact_type"]: row["classification"]
                for row in replan.json()["artifacts"]} == {
            "container-image": "SAME", "helm-chart": "SAME",
        }
        retry = client.post(
            f"/api/imports/{replay_id}/execute", headers=headers,
            json={"destination_plan_id": replan.json()["plan_id"]},
        )
        assert retry.status_code == 202, retry.text
        assert _terminal(client, headers, replay_id)["status"] == "COMPLETED"
        receipt2 = client.get(
            f"/api/imports/{replay_id}/receipt", headers=headers
        )
        assert receipt2.status_code == 200, receipt2.text
        assert {x["artifact_type"]: (x["status"], x["target_digest"])
                for x in receipt2.json()["artifacts"]} == {
            kind: ("SKIPPED", digest) for kind, digest in expected.items()
        }

    observed = asyncio.run(
        NexusService(
            MagicMock(),
            make_settings(tmp_path / "target-mixed-verify", password,
                          url=TARGET_REGISTRY_URL),
        ).inspect_image(ImageReference("htp-e2e-mixed/source", "1.0.0"))
    )
    assert observed.digest == image_digest
    helm = asyncio.run(_helm_service(password).list_charts("htp-helm-target"))
    assert helm.available
    assert any(
        item.name == "htp-mixed" and item.version == "7.1.0"
        and "sha256:" + item.digest == chart_digest
        for item in helm.items
    )
