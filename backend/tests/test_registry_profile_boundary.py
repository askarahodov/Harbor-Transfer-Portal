"""Nexus profiles must never be routed through the Harbor API client."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.harbor import get_harbor_client
from app.schemas.harbor import HarborSelectableProfileResponse


def test_selectable_profile_type_backward_compatible():
    legacy = HarborSelectableProfileResponse(
        id="default", name="Harbor", url="https://harbor.example", is_default=True
    )
    assert legacy.registry_type == "harbor"
    nexus = HarborSelectableProfileResponse(
        id="nexus", name="Nexus", url="https://nexus.example", registry_type="nexus"
    )
    assert nexus.registry_type == "nexus"


def test_nexus_profile_rejected_before_harbor_client_creation():
    request = Request({
        "type": "http",
        "app": SimpleNamespace(state=SimpleNamespace(settings=MagicMock())),
    })
    session = MagicMock()
    service = MagicMock()
    service.get.return_value = SimpleNamespace(registry_type="nexus")
    with patch("app.api.harbor.HarborProfileService", return_value=service):
        dependency = get_harbor_client(request, session, profile_id="nexus-profile")
        with pytest.raises(HTTPException) as error:
            next(dependency)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "registry_type_mismatch"
    service.build_client.assert_not_called()
