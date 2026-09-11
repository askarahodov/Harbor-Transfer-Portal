from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.services.harbor_client import HarborClient, HarborClientError


def test_project_pagination_collects_all_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        payloads = {
            1: [{"project_id": 1, "name": "one"}, {"project_id": 2, "name": "two"}],
            2: [{"project_id": 3, "name": "three"}],
        }
        return httpx.Response(200, json=payloads[page], headers={"X-Total-Count": "3"})

    client = HarborClient(
        base_url="https://harbor.local",
        page_size=2,
        transport=httpx.MockTransport(handler),
    )
    assert [item.name for item in client.list_projects()] == ["one", "two", "three"]


def test_repository_and_artifact_paths_are_encoded() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        if request.url.path.endswith("/repositories"):
            return httpx.Response(200, json=[{"id": 7, "name": "team/app"}])
        return httpx.Response(
            200,
            json={
                "digest": "sha256:" + "a" * 64,
                "type": "IMAGE",
                "tags": [{"name": "1.0.0"}],
            },
        )

    client = HarborClient(base_url="https://harbor.local", transport=httpx.MockTransport(handler))
    assert client.list_repositories("team/name")[0].name == "team/app"
    artifact = client.get_artifact("team/name", "nested/repo", "release/1")
    assert artifact.tags[0].name == "1.0.0"
    assert any("team%2Fname" in path for path in seen)
    assert any("nested%2Frepo" in path and "release%2F1" in path for path in seen)


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(401, "unauthorized"), (403, "forbidden"), (404, "not_found"), (429, "rate_limited"), (503, "harbor_unavailable")],
)
def test_http_errors_are_normalized(status_code: int, expected_code: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="secret raw upstream body")

    client = HarborClient(base_url="https://harbor.local", transport=httpx.MockTransport(handler))
    with pytest.raises(HarborClientError) as exc_info:
        client.system_info()
    assert exc_info.value.code == expected_code
    assert "secret raw upstream body" not in str(exc_info.value)


def test_missing_reference_returns_none_but_forbidden_does_not() -> None:
    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = HarborClient(base_url="https://harbor.local", transport=httpx.MockTransport(missing))
    assert client.reference_digest("p", "repo", "missing") is None

    def forbidden(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = HarborClient(base_url="https://harbor.local", transport=httpx.MockTransport(forbidden))
    with pytest.raises(HarborClientError, match="denied"):
        client.reference_digest("p", "repo", "hidden")


def test_timeout_is_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = HarborClient(base_url="https://harbor.local", transport=httpx.MockTransport(handler))
    with pytest.raises(HarborClientError) as exc_info:
        client.system_info()
    assert exc_info.value.code == "timeout"


def test_custom_ca_and_tls_configuration_are_plumbed() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    custom_ca = Path("/etc/harbor-transfer-portal/ca.crt")
    settings = Settings(
        harbor_url="https://harbor.local",
        harbor_user="robot$user",
        harbor_password="super-secret-password",
        harbor_ca_file=custom_ca,
    )
    client = HarborClient.from_settings(settings, transport=transport)
    assert client.verify == str(custom_ca)

    insecure = HarborClient.from_settings(
        Settings(harbor_url="https://harbor.local", harbor_verify_tls=False),
        transport=transport,
    )
    assert insecure.verify is False
