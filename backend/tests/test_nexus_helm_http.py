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
async def test_direct_helm_repository_acl_isolated_when_global_listing_is_forbidden(
    client_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, _data, digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        if request.url.path == "/repository/helm-private/index.yaml":
            return httpx.Response(403)
        if request.url.path == "/repository/helm-team/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": [{
                    "version": "1.2.3",
                    "digest": digest,
                    "urls": ["demo-1.2.3.tgz"],
                }]}
            }))
        return httpx.Response(404)

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    blocked = await service.list_charts("helm-private")
    allowed = await service.list_charts("helm-team")
    assert not blocked.available and blocked.code == "helm_repository_denied"
    assert blocked.items == ()
    assert "secret-not-for-logs" not in (blocked.message or "")
    assert allowed.available and [(v.name, v.version) for v in allowed.items] == [
        ("demo", "1.2.3"),
    ]
    assert [req.url.path for req in state["calls"]] == [
        "/repository/helm-private/index.yaml",
        "/repository/helm-team/index.yaml",
    ]
    assert all(req.url.host == "nexus.example.test" for req in state["calls"])


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
async def test_helm_upload_polls_delayed_index_without_repeating_post(
    client_service, tmp_path: Path, monkeypatch,
):
    service, state, data, digest = client_service
    state["present"] = False
    seen_after = {"count": 0}
    client = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        if request.url.path == "/service/rest/v1/components":
            state["upload"] = True
            return httpx.Response(204)
        if request.url.path == "/repository/helm-a/index.yaml":
            if state["upload"]:
                seen_after["count"] += 1
            visible = state["upload"] and seen_after["count"] >= 3
            entries = [{"version": "1.2.3", "digest": digest,
                        "urls": ["demo-1.2.3.tgz"]}] if visible else []
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": entries},
            }))
        return httpx.Response(404)

    def factory(**kwargs):
        return client(transport=httpx.MockTransport(handler), **kwargs)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    monkeypatch.setattr("app.services.nexus_helm_http.asyncio.sleep", no_sleep)
    file = tmp_path / "demo-1.2.3.tgz"
    file.write_bytes(data)
    assert await service.upload_chart("helm-a", "demo", "1.2.3", file) == digest
    assert seen_after["count"] == 3
    assert sum(req.method == "POST" for req in state["calls"]) == 1


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"name": "foreign-helm", "format": "helm", "type": "hosted"},
        {"name": "helm-a", "format": "docker", "type": "hosted"},
        {"name": "helm-a", "format": "helm", "type": "group"},
    ],
)
async def test_helm_bridge_empty_index_requires_matching_hosted_repository(
    client_service, monkeypatch: pytest.MonkeyPatch, metadata: dict[str, str],
) -> None:
    service, state, _data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(404)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json=metadata)
        raise AssertionError("Unverified repository identity must not trigger a write")

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    with pytest.raises(HelmServiceError) as error:
        await bridge.inspect_target(
            HelmChartReference("helm-a", "demo", "1.2.3"),
            expected_digest="sha256:" + "a" * 64,
        )
    assert error.value.code == "helm_index_missing"
    assert [request.url.path for request in state["calls"]] == [
        "/repository/helm-a/index.yaml",
        "/service/rest/v1/repositories/helm-a",
    ]
    assert all(request.method == "GET" for request in state["calls"])
    assert all(request.url.host == "nexus.example.test" for request in state["calls"])


@pytest.mark.asyncio
async def test_helm_index_normalizes_absolute_url_without_following_it(client_service):
    service, state, _data, digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        return httpx.Response(200, text=yaml.safe_dump({
            "entries": {"demo": [{
                "version": "1.2.3", "digest": digest,
                "urls": [
                    "http://internal-nexus:8081/repository/helm-a/demo-1.2.3.tgz"
                ],
            }]}
        }))

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    from unittest.mock import patch
    with patch("app.services.nexus_helm_http.httpx.AsyncClient", factory):
        result = await service.list_charts("helm-a")
    assert result.available and len(result.items) == 1
    assert result.items[0].path == "demo-1.2.3.tgz"
    assert [str(req.url) for req in state["calls"]] == [
        "https://nexus.example.test/repository/helm-a/index.yaml"
    ]


@pytest.mark.asyncio
async def test_helm_index_rejects_foreign_repository_path(client_service):
    service, state, _data, digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        return httpx.Response(200, text=yaml.safe_dump({
            "entries": {"demo": [{
                "version": "1.2.3", "digest": digest,
                "urls": [
                    "https://example.invalid/repository/other/demo-1.2.3.tgz"
                ],
            }]}
        }))

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    from unittest.mock import patch
    with patch("app.services.nexus_helm_http.httpx.AsyncClient", factory):
        result = await service.list_charts("helm-a")
    assert result.available and result.items == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_record", [
    {"version": "1.2.3", "digest": "not-sha256", "urls": ["demo-1.2.3.tgz"]},
    {"version": "1.2.3", "digest": "a" * 64,
     "urls": ["https://foreign.invalid/other-package.tgz"]},
    {"version": "1.2.3", "digest": "a" * 64, "urls": []},
])
async def test_malformed_target_index_never_allows_upload_or_absent_plan(
    client_service, tmp_path: Path, monkeypatch, bad_record,
):
    service, state, data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": [bad_record]},
            }))
        raise AssertionError("POST or chart download must not occur")

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    target = HelmChartReference("helm-a", "demo", "1.2.3")
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    with pytest.raises(HelmServiceError) as error:
        await bridge.inspect_target(target)
    assert error.value.code == "helm_index_invalid"

    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_index_invalid"
    assert not state["upload"]
    assert not any(request.method == "POST" for request in state["calls"])
    assert all(request.url.host == "nexus.example.test" for request in state["calls"])


@pytest.mark.asyncio
async def test_duplicate_chart_version_fails_closed_before_target_upload(
    client_service, tmp_path: Path, monkeypatch,
):
    service, state, data, digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": [
                    {"version": "1.2.3", "digest": digest, "urls": ["demo-1.2.3.tgz"]},
                    {"version": "1.2.3", "digest": "f" * 64,
                     "urls": ["demo-1.2.3.tgz"]},
                ]},
            }))
        raise AssertionError("unexpected write or download")

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    with pytest.raises(HelmServiceError) as error:
        await bridge.inspect_target(
            HelmChartReference("helm-a", "demo", "1.2.3"),
            expected_digest="sha256:" + digest,
        )
    assert error.value.code == "helm_index_invalid"
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_index_invalid"
    assert all(request.method == "GET" for request in state["calls"])


@pytest.mark.parametrize("duplicate_name", [
    "demo/Chart.yaml", "demo/templates/config.yaml",
])
def test_duplicate_tar_member_rejected_before_nexus_write(duplicate_name):
    archive_buffer = io.BytesIO()
    metadata = yaml.safe_dump({
        "apiVersion": "v2", "name": "demo", "version": "1.2.3",
    }).encode()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        entry = tarfile.TarInfo("demo/Chart.yaml")
        entry.size = len(metadata)
        archive.addfile(entry, io.BytesIO(metadata))
        if duplicate_name != "demo/Chart.yaml":
            entry = tarfile.TarInfo(duplicate_name)
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"a"))
        entry = tarfile.TarInfo(duplicate_name)
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"b"))
    with pytest.raises(NexusHelmHttpError) as error:
        NexusHelmHttpService._validate_package(
            archive_buffer.getvalue(), "demo", "1.2.3"
        )
    assert error.value.code == "helm_package_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_entries", [
    [
        {"version": "1.2.3", "digest": "a" * 64,
         "urls": ["demo-1.2.3.tgz"]},
        {"version": "1.2.3", "digest": "f" * 64,
         "urls": ["demo-1.2.3.tgz"]},
    ],
    [
        {"version": "1.2.3", "digest": "not-a-digest",
         "urls": ["demo-1.2.3.tgz"]},
    ],
    [
        {"version": "1.2.3", "digest": "a" * 64,
         "urls": ["https://foreign.invalid/untrusted-chart.tgz"]},
    ],
])
async def test_source_download_refuses_ambiguous_index_before_package_request(
    client_service, tmp_path: Path, monkeypatch, invalid_entries,
):
    service, state, _data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": invalid_entries},
            }))
        raise AssertionError("SOURCE must not download ambiguous chart or make writes")

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    destination = tmp_path / "demo-1.2.3.tgz"
    with pytest.raises(NexusHelmHttpError) as error:
        await service.download_chart("helm-a", "demo", "1.2.3", destination)
    assert error.value.code == "helm_index_invalid"
    assert not destination.exists()
    assert [req.url.path for req in state["calls"]] == [
        "/repository/helm-a/index.yaml",
    ]
    assert "secret-not-for-logs" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("index_yaml", [
    # Duplicate chart name hides an earlier version collection.
    "entries:\n  demo:\n    - version: 1.2.3\n      digest: " + "a" * 64
    + "\n      urls: [demo-1.2.3.tgz]\n  demo: []\n",
    # Duplicate digest in a version record hides a checksum conflict.
    "entries:\n  demo:\n    - version: 1.2.3\n      digest: " + "a" * 64
    + "\n      digest: " + "b" * 64 + "\n      urls: [demo-1.2.3.tgz]\n",
    # Duplicate top-level entries can hide the entire first catalog.
    "entries:\n  demo: []\nentries:\n  other: []\n",
])
async def test_duplicate_yaml_mapping_keys_block_source_and_target(
    client_service, monkeypatch, tmp_path: Path, index_yaml: str,
):
    service, state, data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(200, text=index_yaml)
        raise AssertionError("ambiguous Helm YAML must not trigger downloads or writes")

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    # Even exploratory listing must not silently hide duplicate YAML keys.
    catalog = await service.list_charts("helm-a")
    assert not catalog.available and catalog.code == "helm_index_invalid"

    destination = tmp_path / "demo-1.2.3.tgz"
    with pytest.raises(NexusHelmHttpError) as source_error:
        await service.download_chart("helm-a", "demo", "1.2.3", destination)
    assert source_error.value.code == "helm_index_invalid"
    assert not destination.exists()

    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    with pytest.raises(HelmServiceError) as target_error:
        await bridge.inspect_target(HelmChartReference("helm-a", "demo", "1.2.3"))
    assert target_error.value.code == "helm_index_invalid"

    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as upload_error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert upload_error.value.code == "helm_index_invalid"
    assert all(req.method == "GET" for req in state["calls"])
    assert all(req.url.host == "nexus.example.test" for req in state["calls"])


def test_duplicate_chart_yaml_name_or_version_rejected():
    for metadata in (
        b"apiVersion: v2\nname: demo\nname: impostor\nversion: 1.2.3\n",
        b"apiVersion: v2\nname: demo\nversion: 1.2.3\nversion: 2.0.0\n",
    ):
        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
            entry = tarfile.TarInfo("demo/Chart.yaml")
            entry.size = len(metadata)
            archive.addfile(entry, io.BytesIO(metadata))
        with pytest.raises(NexusHelmHttpError) as error:
            NexusHelmHttpService._validate_package(
                archive_buffer.getvalue(), "demo", "1.2.3"
            )
        assert error.value.code == "helm_package_invalid"


def test_helm_archive_rejects_member_flood_before_materialization():
    """More than 1000 headers must fail without caching an unbounded tar index."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        metadata = b"apiVersion: v2\nname: demo\nversion: 1.2.3\n"
        chart = tarfile.TarInfo("demo/Chart.yaml")
        chart.size = len(metadata)
        archive.addfile(chart, io.BytesIO(metadata))
        for i in range(1000):
            directory = tarfile.TarInfo(f"demo/dir-{i}/")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
    with pytest.raises(NexusHelmHttpError) as error:
        NexusHelmHttpService._validate_package(
            buffer.getvalue(), "demo", "1.2.3"
        )
    assert error.value.code == "helm_package_invalid"


def test_helm_archive_rejects_expanded_size_while_scanning(monkeypatch):
    from app.services import nexus_helm_http

    # Keep test archives small; the production cap is 100 MiB expanded.
    monkeypatch.setattr(nexus_helm_http, "_MAX_CHART", 1024)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        metadata = b"apiVersion: v2\nname: demo\nversion: 1.2.3\n"
        chart = tarfile.TarInfo("demo/Chart.yaml")
        chart.size = len(metadata)
        archive.addfile(chart, io.BytesIO(metadata))
        payload = tarfile.TarInfo("demo/templates/oversize.yaml")
        payload.size = 1100
        archive.addfile(payload, io.BytesIO(b"x" * 1100))
    with pytest.raises(NexusHelmHttpError) as error:
        NexusHelmHttpService._validate_package(
            buffer.getvalue(), "demo", "1.2.3"
        )
    assert error.value.code == "helm_package_invalid"


def test_helm_archive_accepts_metadata_after_regular_members():
    """Streaming validation must not assume Chart.yaml is the first tar entry."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        config = b"kind: ConfigMap\n"
        entry = tarfile.TarInfo("demo/templates/config.yaml")
        entry.size = len(config)
        archive.addfile(entry, io.BytesIO(config))
        metadata = b"apiVersion: v2\nname: demo\nversion: 1.2.3\n"
        chart = tarfile.TarInfo("demo/Chart.yaml")
        chart.size = len(metadata)
        archive.addfile(chart, io.BytesIO(metadata))
    NexusHelmHttpService._validate_package(buffer.getvalue(), "demo", "1.2.3")


@pytest.mark.asyncio
async def test_helm_signed_source_digest_is_verified_on_exact_upload_bytes(
    client_service, tmp_path: Path,
):
    service, state, data, digest = client_service
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    # Caller supplies an old digest even though path now points at a valid
    # replacement package. The actual HTTP upload must never happen.
    with pytest.raises(NexusHelmHttpError) as mismatch:
        await service.upload_chart(
            "helm-a", "demo", "1.2.3", package,
            expected_digest="sha256:" + "0" * 64,
        )
    assert mismatch.value.code == "helm_payload_digest_mismatch"
    assert not state["calls"]
    state["present"] = False
    actual = await service.upload_chart(
        "helm-a", "demo", "1.2.3", package,
        expected_digest="sha256:" + digest,
    )
    assert actual == digest
    assert state["upload"]


@pytest.mark.asyncio
async def test_helm_upload_bounded_file_reader_rejects_oversize_before_network(
    client_service, tmp_path: Path, monkeypatch,
):
    from app.services import nexus_helm_http

    service, state, data, _digest = client_service
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    monkeypatch.setattr(nexus_helm_http, "_MAX_CHART", len(data) - 1)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_chart_too_large"
    assert not state["calls"]


@pytest.mark.asyncio
async def test_helm_upload_refuses_symlink_without_network(
    client_service, tmp_path: Path,
):
    service, state, data, _digest = client_service
    real_package = tmp_path / "real-chart.tgz"
    real_package.write_bytes(data)
    link = tmp_path / "demo-1.2.3.tgz"
    link.symlink_to(real_package)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", link)
    assert error.value.code == "helm_package_invalid"
    assert not state["calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("secondary_url", [
    "https://malicious.invalid/collect.tgz",
    "https://[invalid",
    "../demo-1.2.3.tgz",
    "demo-1.2.3.tgz?token=secret",
    42,
])
async def test_helm_secondary_index_url_blocks_source_and_target_before_network(
    client_service, tmp_path: Path, monkeypatch, secondary_url,
):
    service, state, data, digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={"format": "helm", "type": "hosted"})
        if request.url.path == "/repository/helm-a/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": [{
                    "version": "1.2.3", "digest": digest,
                    "urls": ["demo-1.2.3.tgz", secondary_url],
                }]},
            }))
        raise AssertionError("ambiguous Helm index must not download or write")

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    chart = HelmChartReference("helm-a", "demo", "1.2.3")
    bridge = NexusHelmAdapter.__new__(NexusHelmAdapter)
    bridge.client = service
    destination = tmp_path / "source.tgz"
    with pytest.raises(NexusHelmHttpError) as source_error:
        await service.download_chart("helm-a", "demo", "1.2.3", destination)
    assert source_error.value.code == "helm_index_invalid"
    assert not destination.exists()
    with pytest.raises(HelmServiceError) as target_error:
        await bridge.inspect_target(chart, expected_digest="sha256:" + digest)
    assert target_error.value.code == "helm_index_invalid"
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as upload_error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert upload_error.value.code == "helm_index_invalid"
    assert all(request.method == "GET" for request in state["calls"])


@pytest.mark.asyncio
async def test_helm_valid_secondary_same_repository_url_is_permitted(client_service):
    service, state, _data, digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        return httpx.Response(200, text=yaml.safe_dump({
            "entries": {"demo": [{
                "version": "1.2.3", "digest": digest,
                "urls": [
                    "demo-1.2.3.tgz",
                    "https://nexus.example.test/repository/helm-a/demo-1.2.3.tgz",
                ],
            }]},
        }))

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    from unittest.mock import patch
    with patch("app.services.nexus_helm_http.httpx.AsyncClient", factory):
        listed = await service.list_charts("helm-a")
    assert listed.available and len(listed.items) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_url", [
    "https://[invalid",
    "https://nexus.example.test:not-a-port",
    "https://nexus.example.test:0",
    "https://nexus.example.test:65536",
    "https://reader:secret@nexus.example.test",
    "https://nexus.example.test/repository/helm-a",
    "https://nexus.example.test/?token=secret",
    "http://nexus.example.test",
])
async def test_helm_http_origin_must_be_valid_root_https_before_network(
    client_service, unsafe_url,
):
    service, state, _data, _digest = client_service
    service.base_url = unsafe_url
    result = None
    with pytest.raises(NexusHelmHttpError) as error:
        result = await service.list_charts("helm-a")
    assert result is None
    assert error.value.code == "helm_url_invalid"
    assert "secret" not in error.value.message.lower()
    assert not state["calls"]


@pytest.mark.asyncio
async def test_helm_hosted_upload_rejects_metadata_for_different_repository(
    client_service, monkeypatch, tmp_path: Path,
):
    service, state, data, _digest = client_service
    original = REAL_ASYNC_CLIENT

    def handler(request):
        state["calls"].append(request)
        if request.url.path == "/service/rest/v1/repositories/helm-a":
            return httpx.Response(200, json={
                "name": "helm-another", "format": "helm", "type": "hosted",
            })
        raise AssertionError("foreign metadata must not cause Helm writes or index reads")

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_helm_http.httpx.AsyncClient", factory)
    package = tmp_path / "demo-1.2.3.tgz"
    package.write_bytes(data)
    with pytest.raises(NexusHelmHttpError) as error:
        await service.upload_chart("helm-a", "demo", "1.2.3", package)
    assert error.value.code == "helm_repository_read_only"
    assert [call.url.path for call in state["calls"]] == [
        "/service/rest/v1/repositories/helm-a",
    ]
    assert "secret-not-for-logs" not in str(error.value)
