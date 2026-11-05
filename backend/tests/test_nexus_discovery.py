"""Read-only Nexus Docker v2 discovery tests; never require Harbor endpoints."""

from __future__ import annotations

import httpx
import pytest

from app.services.nexus_discovery import NexusDiscoveryService


@pytest.fixture
def discovery(monkeypatch: pytest.MonkeyPatch):
    service = NexusDiscoveryService.__new__(NexusDiscoveryService)
    service.url = "https://nexus.example.test"
    service.username = "transfer-reader"
    service.password = "sensitive-do-not-expose"
    service.verify = True
    service.timeout = httpx.Timeout(2.0)
    seen: list[httpx.Request] = []
    response = {"status": 200, "json": {"repositories": ["team/jaeger", "other/app"]}}
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(response["status"], json=response["json"])

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        "app.services.nexus_discovery.httpx.AsyncClient", factory
    )
    return service, seen, response


@pytest.mark.asyncio
async def test_catalog_returns_filterable_repositories_without_harbor(discovery) -> None:
    service, seen, _response = discovery
    result = await service.catalog("jaeg")
    assert result.available
    assert result.items == ("team/jaeger",)
    assert [request.url.path for request in seen] == ["/v2/_catalog"]
    assert all(request.method == "GET" for request in seen)


@pytest.mark.asyncio
async def test_tags_are_scoped_to_exact_repository(discovery) -> None:
    service, seen, response = discovery
    response["json"] = {"name": "team/jaeger", "tags": ["2.13.0", "2.12.0", "latest"]}
    result = await service.tags("team/jaeger", "2.13")
    assert result.available
    assert result.items == ("2.13.0",)
    assert [request.url.path for request in seen] == ["/v2/team/jaeger/tags/list"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403, 404, 302, 502])
async def test_catalog_unavailable_is_not_silent_empty(discovery, code: int) -> None:
    service, _seen, response = discovery
    response["status"] = code
    result = await service.catalog()
    assert not result.available
    assert result.items == ()
    assert result.message
    assert "sensitive-do-not-expose" not in result.message


@pytest.mark.asyncio
async def test_invalid_repository_does_not_make_network_request(discovery) -> None:
    service, seen, _response = discovery
    with pytest.raises(ValueError):
        await service.tags("../admin")
    assert seen == []


@pytest.mark.asyncio
async def test_untrusted_payload_is_discarded(discovery) -> None:
    service, _seen, response = discovery
    response["json"] = {"repositories": ["team/valid", "../escape", "bad tag"]}
    result = await service.catalog()
    assert result.items == ("team/valid",)
