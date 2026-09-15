import httpx
import pytest

from app.services.harbor_client import HarborClient, HarborClientError


def test_project_page_fetches_only_requested_upstream_page() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.params["page"], request.url.params["page_size"]))
        assert request.url.params["sort"] == "name"
        return httpx.Response(
            200,
            json=[{"project_id": 101, "name": "page-one"}],
            headers={"X-Total-Count": "9000"},
        )

    client = HarborClient(
        base_url="https://harbor.local",
        transport=httpx.MockTransport(handler),
    )

    result = client.list_projects_page(1, 25)

    assert result.total == 9000
    assert [item.name for item in result.items] == ["page-one"]
    assert seen == [("1", "25")]


def test_repository_search_is_forwarded_as_harbor_fuzzy_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "name=~report"
        assert request.url.params["page"] == "2"
        assert request.url.params["page_size"] == "25"
        return httpx.Response(
            200,
            json=[{"id": 1, "name": "team/softrust-report-api"}],
            headers={"X-Total-Count": "26"},
        )

    client = HarborClient(
        base_url="https://harbor.local",
        transport=httpx.MockTransport(handler),
    )

    result = client.list_repositories_page("team", 2, 25, search_needle="report")

    assert result.total == 26
    assert result.items[0].name == "team/softrust-report-api"


def test_artifact_tag_and_digest_search_use_supported_harbor_q() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        return httpx.Response(
            200,
            json=[],
            headers={"X-Total-Count": "0"},
        )

    client = HarborClient(
        base_url="https://harbor.local",
        transport=httpx.MockTransport(handler),
    )

    client.list_artifacts_page("team", "app", 1, 25, search_needle="1.2.3")
    client.list_artifacts_page(
        "team",
        "app",
        1,
        25,
        search_needle="abcdef12",
        search_digest=True,
    )

    assert seen == ["tags=~1.2.3", "digest=~abcdef12"]


def test_paged_browse_rejects_missing_total_header() -> None:
    client = HarborClient(
        base_url="https://harbor.local",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[])),
    )

    with pytest.raises(HarborClientError) as exc_info:
        client.list_projects_page(1, 25)

    assert exc_info.value.code == "invalid_response"


def test_fuzzy_query_delimiters_are_rejected_before_http_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[], headers={"X-Total-Count": "0"})

    client = HarborClient(
        base_url="https://harbor.local",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="unsupported query delimiters"):
        client.list_projects_page(1, 25, search_needle="name=admin")

    assert called is False
