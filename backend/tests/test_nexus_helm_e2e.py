"""Disposable Nexus 3 classic Helm hosted HTTP interoperability (never OCI)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
import pytest
from test_nexus_helm_http import chart_bytes

from app.services.helm_oci_service import HelmChartReference, HelmServiceError, HelmTargetState
from app.services.nexus_helm_http import NexusHelmAdapter, NexusHelmHttpError, NexusHelmHttpService

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1",
    reason="Requires disposable Nexus with Helm hosted repositories and HTTPS proxy",
)


def _service(password: str) -> NexusHelmHttpService:
    service = NexusHelmHttpService.__new__(NexusHelmHttpService)
    service.base_url = os.environ["HTP_NEXUS_HELM_HTTPS_URL"]
    service.username = "admin"
    service.password = password
    service.verify = os.environ["HTP_NEXUS_TLS_CA_FILE"]
    service.timeout = httpx.Timeout(25)
    return service


@pytest.mark.asyncio
async def test_real_classic_helm_source_to_target_and_idempotent_replay(
    tmp_path: Path,
) -> None:
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text().strip()
    source = _service(password)
    target = _service(password)
    package = tmp_path / "htp-test-1.0.0.tgz"
    package.write_bytes(chart_bytes("htp-test", "1.0.0"))
    expected = hashlib.sha256(package.read_bytes()).hexdigest()

    # Provision a real Helm HTTP chart on an isolated disposable SOURCE.
    uploaded = await source.upload_chart("htp-helm-source", "htp-test", "1.0.0", package)
    assert uploaded == expected

    source_index = await source.list_charts("htp-helm-source")
    assert source_index.available
    assert any(item.name == "htp-test" and item.digest == expected
               for item in source_index.items)

    # Offline media boundary: only downloaded .tgz bytes are passed to TARGET.
    handoff = tmp_path / "handoff" / "htp-test-1.0.0.tgz"
    handoff.parent.mkdir()
    entry = await source.download_chart(
        "htp-helm-source", "htp-test", "1.0.0", handoff
    )
    assert entry.digest == expected
    assert hashlib.sha256(handoff.read_bytes()).hexdigest() == expected

    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = target
    destination = HelmChartReference("htp-helm-target", "htp-test", "1.0.0")
    before = await bridge.inspect_target(destination, expected_digest="sha256:" + expected)
    assert before.state == HelmTargetState.ABSENT

    pushed = await bridge.push_chart(
        handoff, destination, source_digest="sha256:" + expected
    )
    assert pushed.target_digest == "sha256:" + expected
    after = await bridge.inspect_target(destination, expected_digest="sha256:" + expected)
    assert after.state == HelmTargetState.SAME_DIGEST

    # Replaying same artifact is classified SAME; a second mutation is blocked.
    with pytest.raises(HelmServiceError) as conflict:
        await bridge.push_chart(
            handoff, destination, source_digest="sha256:" + expected
        )
    assert conflict.value.code == "helm_target_exists"

    with pytest.raises(HelmServiceError) as mismatch:
        await bridge.push_chart(
            handoff, destination, source_digest="sha256:" + "0" * 64
        )
    assert mismatch.value.code == "helm_payload_digest_mismatch"


@pytest.mark.asyncio
async def test_real_helm_repository_is_not_docker_or_oci() -> None:
    password = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"]).read_text().strip()
    service = _service(password)
    # An ordinary Helm repo may expose no index until its first package.
    index = await service.list_charts("htp-helm-target")
    assert index.available or index.code == "helm_index_missing"
    with pytest.raises(NexusHelmHttpError) as err:
        await service.list_charts("../unauthorized")
    assert err.value.code == "helm_repository_invalid"
