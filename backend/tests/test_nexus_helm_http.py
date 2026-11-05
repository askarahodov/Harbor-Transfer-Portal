"""Contract tests for plain Nexus Helm repositories; no OCI transport."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import httpx
import pytest
import yaml

from app.services.helm_oci_service import HelmChartReference, HelmServiceError, HelmTargetState
from app.services.nexus_helm_http import (
    NexusHelmAdapter,
    NexusHelmHttpError,
    NexusHelmHttpService,
)

REAL_ASYNC_CLIENT = httpx.AsyncClient


def chart_bytes(name: str = "demo", version: str = "1.2.3") -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        payload = yaml.safe_dump({"apiVersion": "v2", "name": name, "version": version}).encode()
        info = tarfile.TarInfo(f"{name}/Chart.yaml")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return out.getvalue()


@pytest.fixture
def client_service(monkeypatch):
    service = NexusHelmHttpService.__new__(NexusHelmHttpService)
    service.base_url = "https://nexus.example.test"
    service.username = "reader"
    service.password = "secret-not-for-logs"
    service.verify = True
    service.timeout = httpx.Timeout(2)
    data = chart_bytes()
    digest = hashlib.sha256(data).hexdigest()
    state = {"calls": [], "upload": False, "status": 200, "kind": "hosted"}
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={
                "name": "helm-a", "format": "helm", "type": state["kind"]
            })
        if request.url.path == "/service/rest/v1/components":
            state["upload"] = True
            return httpx.Response(204)
        if request.url.path == "/repository/helm-a/index.yaml":
            entries = ([{
                "name": "demo", "version": "1.2.3", "digest": digest,
                "urls": ["demo-1.2.3.tgz"]
            }] if state["upload"] or request.method == "GET" and state.get("present", True)
                else [])
            return httpx.Response(
                state["status"], text=yaml.safe_dump({"apiVersion": "v1", "entries": {
                    "demo": entries
                }})
            )
        if request.url.path == "/repository/helm-a/demo-1.2.3.tgz":
            return httpx.Response(200, content=data)
        return httpx.Response(404)

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    return service, state, data, digest


@pytest.mark.asyncio
async def test_helm_index_and_download_with_checksum(client_service, tmp_path: Path):
    service, state, _data, digest = client_service
    result = await service.list_charts("helm-a")
    assert result.available and len(result.items) == 1
    destination = tmp_path / "demo-1.2.3.tgz"
    downloaded = await service.download_chart("helm-a", "demo", "1.2.3", destination)
    assert downloaded.digest == digest
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    assert all(request.url.host == "nexus.example.test" for request in state["calls"])


@pytest.mark.asyncio
async def test_helm_index_unauthorized_is_not_empty(client_service):
    service, state, _data, _digest = client_service
    state["status"] = 403
    result = await service.list_charts("helm-a")
    assert not result.available and not result.items
    assert result.code == "helm_repository_denied"
    assert "secret-not-for-logs" not in (result.message or "")


@pytest.mark.asyncio
async def test_helm_index_rejects_foreign_and_traversal_urls(client_service):
    service, state, _data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        return httpx.Response(200, text=yaml.safe_dump({"entries": {
            "demo": [{"version": "1.2.3", "digest": "a" * 64,
                      "urls": ["https://attacker.invalid/demo-1.2.3.tgz"]}]
        }}))

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    from unittest.mock import patch
    with patch("app.services.nexus_helm_http.httpx.AsyncClient", factory):
        result = await service.list_charts("helm-a")
    assert result.available and not result.items
    assert len(state["calls"]) == 0


@pytest.mark.asyncio
async def test_helm_rejects_insecure_url_and_repository_traversal(client_service):
    service, state, _data, _digest = client_service
    service.base_url = "http://nexus.example.test"
    with pytest.raises(NexusHelmHttpError, match="HTTPS"):
        await service.list_charts("helm-a")
    service.base_url = "https://nexus.example.test"
    with pytest.raises(NexusHelmHttpError):
        await service.list_charts("../other")
    assert state["calls"] == []


@pytest.mark.asyncio
async def test_helm_hosted_upload_rechecks_index_and_digest(client_service, tmp_path: Path):
    service, state, data, digest = client_service
    state["present"] = False
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    observed = await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert observed == digest
    assert state["upload"]
    assert any(x.url.path == "/service/rest/v1/components" for x in state["calls"])


@pytest.mark.asyncio
async def test_first_helm_hosted_upload_accepts_missing_index_after_type_check(
    client_service, tmp_path: Path,
):
    service, state, data, digest = client_service
    state["present"] = False
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        if request.url.path == "/service/rest/v1/components":
            state["upload"] = True
            return httpx.Response(204)
        if request.url.path.endswith("/index.yaml") and not state["upload"]:
            return httpx.Response(404)
        return httpx.Response(200, text=yaml.safe_dump({
            "entries": {"demo": [{
                "version": "1.2.3", "digest": digest,
                "urls": ["demo-1.2.3.tgz"]
            }]}
        }))

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    from unittest.mock import patch
    with patch("app.services.nexus_helm_http.httpx.AsyncClient", factory):
        observed = await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert observed == digest and state["upload"]


@pytest.mark.asyncio
async def test_helm_proxy_upload_rejected_before_write(client_service, tmp_path: Path):
    service, state, data, _digest = client_service
    state["kind"] = "proxy"
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_repository_read_only"
    assert not state["upload"]


@pytest.mark.asyncio
async def test_helm_existing_version_not_overwritten(client_service, tmp_path: Path):
    service, state, data, _digest = client_service
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_target_exists"
    assert not state["upload"]


@pytest.mark.asyncio
async def test_helm_invalid_archive_never_uploaded(client_service, tmp_path: Path):
    service, state, _data, _digest = client_service
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(b"not-a-chart")
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_package_invalid"
    assert not state["calls"]


@pytest.mark.asyncio
async def test_helm_bridge_inspects_same_conflict_and_absent(client_service):
    service, state, _data, digest = client_service
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    chart = HelmChartReference("helm-a", "demo", "1.2.3")
    same = await bridge.inspect_target(chart, expected_digest="sha256:" + digest)
    assert same.state == HelmTargetState.SAME_DIGEST
    conflict = await bridge.inspect_target(chart, expected_digest="sha256:" + "0" * 64)
    assert conflict.state == HelmTargetState.CONFLICTING_DIGEST
    state["present"] = False
    absent = await bridge.inspect_target(chart, expected_digest="sha256:" + digest)
    assert absent.state == HelmTargetState.ABSENT


@pytest.mark.asyncio
async def test_helm_bridge_pull_for_signed_bundle(client_service, tmp_path: Path):
    service, _state, _data, digest = client_service
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    chart = HelmChartReference("helm-a", "demo", "1.2.3")
    result = await bridge.pull_chart(chart, tmp_path / "charts")
    assert result.source_digest == "sha256:" + digest
    assert result.package.sha256 == digest
    assert result.package.path.is_file()


@pytest.mark.asyncio
async def test_helm_bridge_push_refuses_digest_mismatch_before_mutation(
    client_service, tmp_path: Path,
):
    service, state, data, _digest = client_service
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    file = tmp_path / "demo-1.2.3.tgz"
    file.write_bytes(data)
    target = HelmChartReference("helm-a", "demo", "1.2.3")
    with pytest.raises(HelmServiceError) as error:
        await bridge.push_chart(file, target, source_digest="sha256:" + "0" * 64)
    assert error.value.code == "helm_payload_digest_mismatch"
    assert not state["calls"]


@pytest.mark.asyncio
async def test_helm_bridge_push_verifies_target_digest(client_service, tmp_path: Path):
    service, state, data, digest = client_service
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    state["present"] = False
    file = tmp_path / "demo-1.2.3.tgz"
    file.write_bytes(data)
    target = HelmChartReference("helm-a", "demo", "1.2.3")
    result = await bridge.push_chart(file, target, source_digest="sha256:" + digest)
    assert result.target_digest == "sha256:" + digest
    assert result.digest_matches_source is True
    assert state["upload"]


@pytest.mark.asyncio
async def test_helm_bridge_never_performs_overwrite(client_service, tmp_path: Path):
    service, state, data, digest = client_service
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    file = tmp_path / "demo-1.2.3.tgz"
    file.write_bytes(data)
    with pytest.raises(HelmServiceError) as error:
        await bridge.push_chart(
            file, HelmChartReference("helm-a", "demo", "1.2.3"),
            source_digest="sha256:" + digest, allow_existing=True,
        )
    assert error.value.code == "helm_overwrite_unsupported"
    assert not state["calls"]


@pytest.mark.asyncio
async def test_helm_bridge_empty_hosted_404_is_safe_new(client_service):
    service, state, _data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(404)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        return httpx.Response(404)

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    from unittest.mock import patch
    with patch("app.services.nexus_helm_http.httpx.AsyncClient", factory):
        result = await bridge.inspect_target(HelmChartReference("helm-a", "demo", "1.2.3"))
    assert result.state == HelmTargetState.ABSENT
    assert [req.url.path for req in state["calls"]] == [
        "/repository/helm-a/index.yaml",
        "/service/rest/v1/repositories/helm-a",
    ]
