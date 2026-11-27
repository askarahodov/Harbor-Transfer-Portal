"""Harbor push capability must use the exact project/image repository token scope."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.harbor_destination_validator import HarborDestinationValidator


def _jwt(repository: str, actions: list[str]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps({
        "access": [
            {"type": "repository", "name": repository, "actions": actions},
        ],
    }).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("granted_repo", "granted_actions", "expected_write"),
    [
        ("test/node", ["pull", "push"], True),
        ("test/node", ["pull"], False),
        ("test", ["pull", "push"], False),
        ("other/node", ["pull", "push"], False),
    ],
)
async def test_harbor_token_checks_exact_root_image_not_project(
    monkeypatch: pytest.MonkeyPatch,
    granted_repo: str,
    granted_actions: list[str],
    expected_write: bool,
) -> None:
    """A project-only token or a different repository never grants push."""
    seen: list[httpx.Request] = []
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"token": _jwt(granted_repo, granted_actions)})

    def client_factory(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs.get("verify") is True
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    validator = object.__new__(HarborDestinationValidator)
    validator._base_url = "https://harbor.example.test"
    validator._resolved = SimpleNamespace(
        username="service-robot", password="SECRET-NEVER-PUBLIC",
        verify_tls=True, ca_file=None,
    )
    validator.settings = SimpleNamespace(
        harbor_read_timeout_seconds=5, harbor_connect_timeout_seconds=5,
    )
    validator._project_cache = {}
    validator._write_cache = {}
    monkeypatch.setattr(validator, "_project_exists", lambda project: project == "test")

    capability = await validator.validate("test", "test/node")
    assert capability.project_exists is True
    assert capability.write_allowed is expected_write
    assert capability.error_code == (
        None if expected_write else "import_destination_write_forbidden"
    )
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.host == "harbor.example.test"
    assert seen[0].url.path == "/service/token"
    assert seen[0].url.params["scope"] == "repository:test/node:pull,push"
    assert seen[0].url.params["service"] == "harbor-registry"
    assert "SECRET-NEVER-PUBLIC" not in (capability.message or "")

    # Within a plan lifetime the exact approved repository is cached;
    # a different image must not inherit this capability.
    second = await validator.validate("test", "test/node")
    assert second.write_allowed is expected_write
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repository", [
    "test", "test/", "other/node", "other/test/node",
])
async def test_harbor_validator_rejects_bare_or_foreign_project_scope_before_http(
    monkeypatch: pytest.MonkeyPatch, repository: str,
) -> None:
    validator = object.__new__(HarborDestinationValidator)
    def reject_http(**kwargs):  # type: ignore[no-untyped-def]
        pytest.fail("Invalid project/repository mapping must not call Harbor")
    monkeypatch.setattr(httpx, "Client", reject_http)
    result = await validator.validate("test", repository)
    assert not result.project_exists
    assert not result.write_allowed
    assert result.error_code == "import_destination_repository_invalid"
    assert result.message is not None
    assert "project/image" in result.message
