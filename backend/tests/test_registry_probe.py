"""Regression tests for Nexus connection probe; no repository/tag must be requested."""

from __future__ import annotations

import httpx
import pytest

from app.services.harbor_settings import EffectiveHarborSettings
from app.services.registry_probe import probe_docker_v2, probe_helm_http


def profile(url: str = "https://nexus.example.test", password: str | None = "secret"):
    return EffectiveHarborSettings(
        url=url, username="operator", password=password, verify_tls=True, ca_file=None
    )


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return original(transport=transport, **kwargs)

    monkeypatch.setattr("app.services.registry_probe.httpx.AsyncClient", factory)


async def probe(p):
    return await probe_docker_v2(p, connect_timeout=2, read_timeout=2)


@pytest.mark.asyncio
async def test_empty_docker_registry_succeeds_without_artifact(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, headers={"Docker-Distribution-API-Version": "registry/2.0"})

    mock_client(monkeypatch, handler)
    result = await probe(profile())
    assert result.ok and result.code == "registry_connection_ok"
    assert paths == ["/v2/"]


@pytest.mark.asyncio
async def test_bearer_challenge_verifies_credentials_without_repository(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/service/token":
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"token": "opaque-test-token"})
        if request.headers.get("authorization") == "Bearer opaque-test-token":
            return httpx.Response(200, headers={"Docker-Distribution-API-Version": "registry/2.0"})
        return httpx.Response(
            401,
            headers={
                "WWW-Authenticate": 'Bearer realm="https://nexus.example.test/service/token",service="nexus"'
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe(profile())
    assert result.ok and result.code == "registry_connection_ok"
    assert paths == ["/v2/", "/service/token", "/v2/"]


@pytest.mark.asyncio
async def test_bearer_token_html_200_is_not_docker_registry(monkeypatch):
    def handler(request):
        if request.url.path == "/service/token":
            return httpx.Response(200, json={"token": "opaque-test-token"})
        if request.headers.get("authorization") == "Bearer opaque-test-token":
            return httpx.Response(200, text="<html>Not a registry</html>")
        return httpx.Response(
            401,
            headers={
                "WWW-Authenticate": (
                    'Bearer realm="https://nexus.example.test/service/token",service="nexus"'
                )
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe(profile())
    assert not result.ok and result.code == "registry_invalid_endpoint"


@pytest.mark.asyncio
async def test_bad_password_returns_safe_failure(monkeypatch):
    def handler(request):
        if request.url.path == "/service/token":
            return httpx.Response(401)
        return httpx.Response(
            401,
            headers={
                "WWW-Authenticate": 'Bearer realm="https://nexus.example.test/service/token"'
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe(profile(password="sensitive-secret"))
    assert not result.ok and result.code == "registry_auth_failed"
    assert "sensitive-secret" not in result.message


@pytest.mark.asyncio
async def test_wrong_endpoint_does_not_report_success(monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(404))
    result = await probe(profile())
    assert not result.ok and result.code == "registry_invalid_endpoint"


@pytest.mark.asyncio
async def test_html_ui_200_is_not_docker_registry(monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(200, text="<html>Nexus UI</html>"))
    result = await probe(profile())
    assert not result.ok and result.code == "registry_invalid_endpoint"


@pytest.mark.asyncio
async def test_missing_credentials_does_not_report_authenticated_success(monkeypatch):
    mock_client(
        monkeypatch,
        lambda request: httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="registry"'}),
    )
    result = await probe(profile(password=None))
    assert not result.ok and result.code == "registry_auth_required"


@pytest.mark.asyncio
async def test_foreign_token_realm_is_rejected_without_forwarding_credentials(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer realm="https://attacker.example/token"'},
        )

    mock_client(monkeypatch, handler)
    result = await probe(profile())
    assert not result.ok and result.code == "registry_auth_unsupported"
    assert paths == ["/v2/"]


@pytest.mark.asyncio
async def test_nexus_ui_url_is_rejected_without_request(monkeypatch):
    result = await probe(profile(url="https://nexus.example.test/#browse"))
    assert not result.ok and result.code == "registry_invalid_endpoint"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://[invalid",
    "https://nexus.example.test:not-a-port",
    "https://nexus.example.test:0",
])
async def test_invalid_registry_origin_is_rejected_without_network(monkeypatch, url):
    def forbidden_client(**_kwargs):
        raise AssertionError("malformed origin should not reach HTTP")

    monkeypatch.setattr("app.services.registry_probe.httpx.AsyncClient", forbidden_client)
    result = await probe(profile(url=url))
    assert not result.ok
    assert result.code == "registry_invalid_endpoint"
    assert url not in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("realm", [
    "https://[invalid",
    "https://nexus.example.test:not-a-port/token",
    "https://nexus.example.test:0/token",
])
async def test_invalid_bearer_realm_never_receives_credentials(monkeypatch, realm):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            401, headers={"WWW-Authenticate": f'Bearer realm="{realm}"'},
        )

    mock_client(monkeypatch, handler)
    result = await probe(profile(password="secret-not-for-errors"))
    assert not result.ok
    assert result.code == "registry_auth_unsupported"
    assert "secret-not-for-errors" not in result.message
    assert paths == ["/v2/"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate",
    "ConnectError: certificate verify failed",
])
async def test_tls_cert_failure_with_flattened_httpcore_error_is_not_network_failure(
    monkeypatch, message,
):
    def handler(request):
        raise httpx.ConnectError(message, request=request)

    mock_client(monkeypatch, handler)
    observed = await probe(profile())
    assert not observed.ok
    assert observed.code == "registry_tls_failed"
    assert "self-signed" not in observed.message
    assert "nexus.example.test" not in observed.message


@pytest.mark.asyncio
async def test_generic_connect_error_remains_unavailable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    mock_client(monkeypatch, handler)
    result = await probe(profile())
    assert not result.ok
    assert result.code == "registry_unavailable"


@pytest.mark.asyncio
async def test_helm_probe_reads_exact_repository_metadata_and_index_only(monkeypatch):
    paths = []

    def handler(request):
        paths.append((request.method, request.url.path))
        if request.url.path == "/service/rest/v1/repositories/helm-hosted":
            return httpx.Response(200, json={
                "name": "helm-hosted", "format": "helm", "type": "hosted",
            })
        if request.url.path == "/repository/helm-hosted/index.yaml":
            return httpx.Response(200, text="apiVersion: v1\nentries: {}\n")
        return httpx.Response(404)

    mock_client(monkeypatch, handler)
    result = await probe_helm_http(profile(), "helm-hosted", connect_timeout=2, read_timeout=2)
    assert result.ok
    assert result.code == "helm_repository_ok"
    assert paths == [
        ("GET", "/service/rest/v1/repositories/helm-hosted"),
        ("GET", "/repository/helm-hosted/index.yaml"),
    ]


@pytest.mark.asyncio
async def test_helm_probe_respects_existing_tls_false_and_empty_index(monkeypatch):
    verifies = []
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "name": "helm-proxy", "format": "helm", "type": "proxy",
        }) if request.url.path.startswith("/service/rest/")
        else httpx.Response(404)
    )

    def factory(**kwargs):
        verifies.append(kwargs["verify"])
        return original(transport=transport, **kwargs)

    monkeypatch.setattr("app.services.registry_probe.httpx.AsyncClient", factory)
    current = EffectiveHarborSettings(
        url="https://nexus.example.test", username="operator",
        password="unchanged-secret", verify_tls=False, ca_file=None,
    )
    result = await probe_helm_http(
        current, "helm-proxy", connect_timeout=2, read_timeout=2,
    )
    assert result.ok
    assert result.code == "helm_index_missing"
    assert current.verify_tls is False
    assert verifies == [False]
    assert "unchanged-secret" not in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [
    (401, "helm_repository_denied"),
    (403, "helm_repository_denied"),
    (404, "helm_repository_missing"),
    (302, "helm_repository_unavailable"),
])
async def test_helm_probe_fails_closed_on_identity_access(monkeypatch, status, expected):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(status, headers={"Location": "https://other.test"} if status == 302 else {})
    mock_client(monkeypatch, handler)
    result = await probe_helm_http(profile(), "helm-hosted", connect_timeout=2, read_timeout=2)
    assert not result.ok
    assert result.code == expected
    assert paths == ["/service/rest/v1/repositories/helm-hosted"]


@pytest.mark.asyncio
async def test_helm_probe_rejects_docker_repository_identity(monkeypatch):
    mock_client(monkeypatch, lambda _request: httpx.Response(200, json={
        "name": "helm-hosted", "format": "docker", "type": "hosted",
    }))
    result = await probe_helm_http(profile(), "helm-hosted", connect_timeout=2, read_timeout=2)
    assert not result.ok
    assert result.code == "helm_repository_format_mismatch"


@pytest.mark.asyncio
async def test_helm_probe_rejects_ui_path_and_malformed_repository_before_network(monkeypatch):
    def unexpected(**_kwargs):
        raise AssertionError("Unsafe Helm endpoint must not contact registry")
    monkeypatch.setattr("app.services.registry_probe.httpx.AsyncClient", unexpected)
    a = await probe_helm_http(
        profile(url="https://nexus.example.test/repository/helm-hosted"),
        "helm-hosted", connect_timeout=2, read_timeout=2,
    )
    b = await probe_helm_http(
        profile(), "../secrets", connect_timeout=2, read_timeout=2,
    )
    assert not a.ok and a.code == "helm_url_invalid"
    assert not b.ok and b.code == "helm_repository_invalid"
