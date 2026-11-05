"""Nexus TARGET preflight must fail closed without Harbor API or credential leaks."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services.nexus_destination_validator import NexusDestinationValidator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (200, {"Docker-Distribution-API-Version": "registry/2.0"}, None),
        (200, {}, "nexus_url_invalid"),
        (401, {"WWW-Authenticate": 'Basic realm="Nexus"'}, "nexus_auth_failed"),
        (403, {}, "nexus_auth_failed"),
        (302, {"Location": "https://other.example.invalid/"}, "nexus_registry_connection_failed"),
        (404, {}, "nexus_registry_connection_failed"),
    ],
)
async def test_docker_target_preflight_is_read_only_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    expected: str | None,
) -> None:
    seen: list[httpx.Request] = []
    actual_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers=headers)

    def client_factory(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return actual_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(
        "app.services.nexus_destination_validator.httpx.AsyncClient",
        client_factory,
    )
    validator = NexusDestinationValidator.__new__(NexusDestinationValidator)
    validator.settings = Settings(_env_file=None)
    validator._url = "https://target.example.invalid"
    validator._registry_host = "target.example.invalid"
    validator._username = "operator"
    validator._password = "sensitive-password-not-for-logs"
    validator._verify = True

    result = await validator.validate("test-project", "test-project/image")
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://target.example.invalid/v2/"
    assert result.project_exists is (expected is None)
    assert result.write_allowed is (expected is None)
    assert result.error_code == expected
    assert "sensitive-password-not-for-logs" not in (result.message or "")


@pytest.mark.asyncio
async def test_target_credentials_never_sent_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_client(**kwargs):  # type: ignore[no-untyped-def]
        pytest.fail("HTTP Basic Auth must be rejected before creating an HTTP client")

    monkeypatch.setattr(
        "app.services.nexus_destination_validator.httpx.AsyncClient",
        forbidden_client,
    )
    validator = NexusDestinationValidator.__new__(NexusDestinationValidator)
    validator.settings = Settings(_env_file=None)
    validator._url = "http://target.example.invalid"
    validator._registry_host = "target.example.invalid"
    validator._username = "operator"
    validator._password = "sensitive-password-not-for-logs"
    validator._verify = False

    result = await validator.validate("test-project", "test-project/image")
    assert not result.project_exists
    assert not result.write_allowed
    assert result.error_code == "nexus_insecure_transport"
    assert "sensitive-password-not-for-logs" not in (result.message or "")
