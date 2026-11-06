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
        custom = response.get("custom_handler")
        if custom is not None:
            return custom(request)
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
                "items": [{"repository": "docker-all", "name": "jaegertracing/jaeger", "version": "2.13.0"}],
                "continuationToken": None,
            })
        return httpx.Response(200, json={
            "items": [
                {"repository": "docker-all", "name": "jaegertracing/jaeger", "version": "2.12.0"},
                {"repository": "docker-all", "name": "other/tool", "version": "3.0"},
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
async def test_rest_search_scopes_acl_per_configured_docker_repository(
    discovery, monkeypatch,
) -> None:
    service, seen, _response = discovery
    service.search_url = "https://nexus-ui.example.test"
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        repository = request.url.params.get("repository")
        if repository == "docker-denied":
            return httpx.Response(403)
        contents = {
            "docker-hosted": [{"repository": "docker-hosted", "name": "team/app", "version": "v1"}],
            "docker-group": [{"repository": "docker-group", "name": "team/worker", "version": "v2"}],
        }
        return httpx.Response(200, json={
            "items": contents.get(repository, []),
            "continuationToken": None,
        })

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_discovery.httpx.AsyncClient", factory)

    service.search_repository = "docker-hosted"
    first = await service.catalog()
    service.search_repository = "docker-denied"
    forbidden = await service.catalog()
    service.search_repository = "docker-group"
    last = await service.catalog()
    assert first.available and first.items == ("team/app",)
    assert not forbidden.available and forbidden.items == ()
    assert "sensitive-do-not-expose" not in (forbidden.message or "")
    assert last.available and last.items == ("team/worker",)
    assert [r.url.params["repository"] for r in seen] == [
        "docker-hosted", "docker-denied", "docker-group",
    ]
    assert all(r.url.host == "nexus-ui.example.test" for r in seen)
    assert all(r.method == "GET" for r in seen)


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
async def test_catalog_search_reads_later_pages_not_only_first_hundred(discovery) -> None:
    service, seen, response = discovery
    first = [f"other/image-{i:03d}" for i in range(100)]

    def handler(request: httpx.Request) -> httpx.Response:
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

    response["custom_handler"] = handler
    result = await service.catalog("softrust")
    assert result.available
    assert result.items == ("softrust/internal-app", "softrust/worker")
    assert result.truncated is False
    assert len(seen) == 2
    assert seen[0].url.params["n"] == "100"
    assert seen[1].url.params["last"] == first[-1]
    assert all(request.method == "GET" for request in seen)


@pytest.mark.asyncio
async def test_catalog_caps_unsupported_infinite_pagination(discovery) -> None:
    service, seen, response = discovery

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"repositories": ["other/stuck"]},
            headers={"Link": '</v2/_catalog?n=100&last=other/stuck>; rel="next"'},
        )

    response["custom_handler"] = handler
    result = await service.catalog("softrust")
    assert not result.available
    assert result.items == ()
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_catalog_never_sends_credentials_over_http(discovery) -> None:
    service, seen, _response = discovery
    service.url = "http://nexus.example.test"
    result = await service.catalog()
    assert not result.available
    assert seen == []
    assert "HTTPS" in (result.message or "")


@pytest.mark.asyncio
async def test_rest_search_never_sends_credentials_over_http(discovery) -> None:
    service, seen, _response = discovery
    service.search_url = "http://nexus-ui.example.test"
    service.search_repository = "docker-all"
    result = await service.catalog()
    assert not result.available
    assert seen == []
    assert "HTTPS" in (result.message or "")


@pytest.mark.asyncio
async def test_inventory_returns_only_visible_docker_and_helm_repositories(
    discovery,
) -> None:
    service, seen, response = discovery
    service.search_url = "https://nexus-ui.example.test"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"name": "docker-a", "format": "docker", "type": "hosted"},
            {"name": "helm-b", "format": "helm", "type": "hosted"},
            {"name": "docker-proxy", "format": "docker", "type": "proxy"},
            {"name": "maven-releases", "format": "maven2", "type": "hosted"},
            {"name": "raw", "format": "raw", "type": "hosted"},
            {"name": "invalid", "format": "helm", "type": "unexpected"},
        ])

    response["custom_handler"] = handler
    result = await service.inventory()
    assert result.available
    assert [(i.name, i.format, i.kind) for i in result.items] == [
        ("docker-a", "docker", "hosted"),
        ("docker-proxy", "docker", "proxy"),
        ("helm-b", "helm", "hosted"),
    ]
    assert [request.url.path for request in seen] == ["/service/rest/v1/repositories"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 502])
async def test_inventory_unavailable_never_claims_empty_registry(
    discovery, status: int,
) -> None:
    service, _seen, response = discovery
    service.search_url = "https://nexus-ui.example.test"
    response["status"] = status
    result = await service.inventory()
    assert not result.available
    assert result.items == ()
    assert result.message


@pytest.mark.asyncio
async def test_inventory_requires_configured_https_ui(discovery) -> None:
    service, seen, _response = discovery
    result = await service.inventory()
    assert not result.available
    service.search_url = "http://nexus-ui.example.test"
    result = await service.inventory()
    assert not result.available
    assert seen == []


@pytest.mark.asyncio
async def test_rest_search_discards_foreign_acl_results_even_if_server_ignores_filter(
    discovery, monkeypatch,
) -> None:
    service, requests, _response = discovery
    service.search_url = "https://nexus-ui.example.test"
    service.search_repository = "docker-hosted"
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "items": [
                {"repository": "docker-hosted", "name": "team/allowed", "version": "v1"},
                {"repository": "docker-denied", "name": "team/private", "version": "secret"},
                {"name": "team/unattributed", "version": "v2"},
                {"repository": "helm-hosted", "name": "team/chart", "version": "v3"},
            ],
            "continuationToken": None,
        })

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_discovery.httpx.AsyncClient", factory)
    names = await service.catalog()
    tags = await service.tags("team/allowed")
    foreign = await service.tags("team/private")
    assert names.available and names.items == ("team/allowed",)
    assert tags.available and tags.items == ("v1",)
    assert foreign.available and foreign.items == ()
    assert len(requests) == 3
    assert all(r.url.params["repository"] == "docker-hosted" for r in requests)


@pytest.mark.asyncio
async def test_rest_search_rejects_cyclic_continuation_token(discovery, monkeypatch):
    service, requests, _response = discovery
    service.search_url = "https://nexus-ui.example.test"
    service.search_repository = "docker-hosted"
    original = REAL_ASYNC_CLIENT

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "items": [{"repository": "docker-hosted", "name": "team/app", "version": "v1"}],
            "continuationToken": "same-token",
        })

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.nexus_discovery.httpx.AsyncClient", factory)
    result = await service.catalog()
    assert not result.available
    assert result.items == ()
    assert len(requests) == 2
    assert "пагинация" in (result.message or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://nexus-ui.example.test",
    "https://[invalid",
    "https://nexus-ui.example.test:0",
    "https://nexus-ui.example.test/secret/path",
])
async def test_rest_search_rejects_unsafe_ui_origin_before_network(
    discovery, url,
) -> None:
    service, requests, _response = discovery
    service.search_url = url
    service.search_repository = "docker-hosted"
    result = await service.catalog()
    assert not result.available and result.items == ()
    assert not requests


@pytest.mark.asyncio
async def test_inventory_ignores_malformed_repo_names(discovery):
    service, _requests, response = discovery
    service.search_url = "https://nexus-ui.example.test"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"name": "docker-allowed", "format": "docker", "type": "hosted"},
            {"name": "secret/other-repo", "format": "docker", "type": "hosted"},
            {"name": "bad name", "format": "docker", "type": "hosted"},
            {"name": "helm-ok", "format": "helm", "type": "proxy"},
        ])

    response["custom_handler"] = handler
    result = await service.inventory()
    assert result.available
    assert [(i.name, i.format) for i in result.items] == [
        ("docker-allowed", "docker"), ("helm-ok", "helm"),
    ]
