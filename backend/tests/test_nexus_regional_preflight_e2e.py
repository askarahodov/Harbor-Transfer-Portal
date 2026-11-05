"""Safe read-only regional preflight against disposable real versioned Nexus.

This is not real corporate acceptance. Uploads below seed disposable fixtures
before executing the collector; the collector itself performs GET and HEAD only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from integration.nexus_regional_preflight import run_document
from test_nexus_helm_e2e import _service
from test_nexus_helm_http import chart_bytes
from test_nexus_legacy_e2e import make_settings, seeded_schema2_image

from app.services.nexus_service import NexusService

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1",
    reason="Only on isolated disposable Nexus E2E runner",
)


def test_regional_read_only_preflight_on_two_formats_and_two_connectors(
    tmp_path: Path,
) -> None:
    secret = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"])
    password = secret.read_text(encoding="utf-8").strip()
    ca = os.environ["HTP_NEXUS_TLS_CA_FILE"]

    # Setup phase only: publish test fixtures into the disposable namespaces.
    settings = make_settings(tmp_path / "seed", password)
    source = NexusService(MagicMock(), settings)
    image_digest = seeded_schema2_image(
        source, settings.skopeo_payload_root / "seed", password,
    )
    package = tmp_path / "region-preflight-1.0.0.tgz"
    package.write_bytes(chart_bytes("region-preflight", "1.0.0"))
    chart_digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert asyncio.run(_service(password).upload_chart(
        "htp-helm-source", "region-preflight", "1.0.0", package,
    )) == chart_digest

    # The real runner has HTTPS reverse proxies (CA/SAN verified) on 8443/8444.
    # Docker manifest HEAD may legitimately challenge for Bearer rather than
    # accepting preemptive Basic; report UNVERIFIED, never a false PASS.
    document = {
        "environment": "disposable-nexus-github-actions",
        "operator": "automated-test",
        "change_id": "TEST-ONLY",
        "commit_sha": "c55fc355823306ada9b5f9536fca4508ff675f2d",
        "probes": [
            {
                "id": "docker-source", "contour": "SOURCE", "format": "docker",
                "kind": "hosted", "repository": "htp-e2e",
                "endpoint": "https://127.0.0.1:8443", "username": "admin",
                "password_file": str(secret), "ca_file": ca,
                "image": "htp-e2e/source", "tag": "1.0.0",
                "expected_digest": image_digest,
            },
            {
                "id": "helm-source", "contour": "SOURCE", "format": "helm",
                "kind": "hosted", "repository": "htp-helm-source",
                "endpoint": "https://127.0.0.1:8444", "username": "admin",
                "password_file": str(secret), "ca_file": ca,
                "chart": "region-preflight", "version": "1.0.0",
                "expected_sha256": chart_digest,
            },
        ],
    }
    report = run_document(document)
    assert report["release_verdict"] == "NO-GO"
    assert report["entries"][0]["checks"][0]["code"] == "docker_v2_reachable"
    assert report["entries"][1]["checks"][-1]["code"] == "package_sha256_verified"
    assert report["entries"][1]["checks"][-1]["status"] == "PASS"
    assert report["preflight_status"] in {"PASS", "INCOMPLETE"}
    assert password not in json.dumps(report)
    assert "Authorization" not in json.dumps(report)
