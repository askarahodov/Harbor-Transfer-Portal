"""Read-only Nexus Docker v2 discovery tests; never require Harbor endpoints."""

from __future__ import annotations

import httpx
import pytest

from app.services.nexus_discovery import NexusDiscoveryService

REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def discovery(monkeypatch: pytest.MonkeyPatch):
    service = NexusDiscoveryService.__new__(NexusDiscoveryService)
    service.url = "https://nexus.example.test"
    service.search_url = None
    service.search_repository = None
    service.username = "transfer-reader"
    service.password = "sensitive-do-not-expose"
    service.verify = True
    service.timeout = httpx.Timeout(2.0)
    seen: list[httpx.Request] = []
    response = {"status": 200, "json": {"repositories": ["team/jaeger", "other/app"]}}
    original = REAL_ASYNC_CLIENT

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


@pytest.mark.asyncio
async def test_nexus_rest_search_uses_configured_repository_and_pagination(
    discovery, monkeypatch
) -> None:
    service, _seen, _response = discovery
    service.search_url = "https://nexus-ui.example.test"
    service.search_repository = "docker-all"
    seen: list[httpx.Request] = []
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "continuationToken" in request.url.params:
            return httpx.Response(200, json={
                "items": [{"name": "jaegertracing/jaeger", "version": "2.13.0"}],
                "continuationToken": None,
            })
        return httpx.Response(200, json={
            "items": [
                {"name": "jaegertracing/jaeger", "version": "2.12.0"},
                {"name": "other/tool", "version": "3.0"},
            ],
            "continuationToken": "next",
        })

    def client_factory(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        "app.services.nexus_discovery.httpx.AsyncClient", client_factory
    )
    names = await service.catalog("jaeger")
    assert names.available
    assert names.items == ("jaegertracing/jaeger",)
    tags = await service.tags("jaegertracing/jaeger", "2.13")
    assert tags.available
    assert tags.items == ("2.13.0",)
    assert all(request.url.path == "/service/rest/v1/search" for request in seen)
    assert all(request.url.params["repository"] == "docker-all" for request in seen)


@pytest.mark.asyncio
async def test_nexus_rest_browse_access_denied_falls_back_to_manual(
    discovery, monkeypatch
) -> None:
    service, _seen, _response = discovery
    service.search_url = "https://nexus-ui.example.test"
    service.search_repository = "docker-all"
    original = REAL_ASYNC_CLIENT

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        return original(
            transport=httpx.MockTransport(lambda request: httpx.Response(403)),
            **kwargs,
        )

    monkeypatch.setattr(
        "app.services.nexus_discovery.httpx.AsyncClient", factory
    )
    result = await service.catalog()
    assert not result.available
    assert result.items == ()
    assert "repository:tag" in (result.message or "")

@pytest.mark.asyncio
async def test_catalog_search_reads_later_pages_not_only_first_hundred(
    discovery, monkeypatch
) -> None:
    service, _seen, _response = discovery
    original = httpx.AsyncClient
    seen: list[httpx.Request] = []
    first = [f"other/image-{i:03d}" for i in range(100)]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("last"):
            assert request.url.params["last"] == first[-1]
            return httpx.Response(
                200, json={"repositories": ["softrust/internal-app", "softrust/worker"]}
            )
        return httpx.Response(
            200,
            json={"repositories": first},
            headers={"Link": '</v2/_catalog?n=100&last=other/image-099>; rel="next"'},
        )

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_discovery.httpx.AsyncClient", factory)
    result = await service.catalog("softrust")
    assert result.available
    assert result.items == ("softrust/internal-app", "softrust/worker")
    assert result.truncated is False
    assert len(seen) == 2
    assert seen[0].url.params["n"] == "100"
    assert seen[1].url.params["last"] == first[-1]
    assert all(request.method == "GET" for request in seen)


@pytest.mark.asyncio
async def test_catalog_caps_unsupported_infinite_pagination(discovery, monkeypatch) -> None:
    service, _seen, _response = discovery
    original = httpx.AsyncClient
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"repositories": ["other/stuck"]},
            headers={"Link": '</v2/_catalog?n=100&last=other/stuck>; rel="next"'},
        )

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_discovery.httpx.AsyncClient", factory)
    result = await service.catalog("softrust")
    assert not result.available
    assert result.items == ()
    assert len(requests) == 2
