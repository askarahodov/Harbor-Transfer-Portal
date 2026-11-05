"""Exact Nexus inspection validates input/profile and never invokes Harbor browse."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.registry_source import (
    ExactSourceInspectionRequest,
    inspect_exact_source,
    nexus_helm_catalog,
)
from app.services.nexus_helm_http import HelmChartVersion, HelmHttpResult
from app.services.nexus_service import ImageInspection


@pytest.fixture
def context():
    request = Request({
        "type": "http",
        "app": SimpleNamespace(state=SimpleNamespace(settings=MagicMock())),
    })
    return request, MagicMock()


@pytest.mark.asyncio
async def test_exact_inspection_reads_digest_without_catalog(context):
    request, session = context
    profiles = MagicMock()
    profiles.operation_snapshot.return_value = SimpleNamespace(
        id="nexus-profile", registry_type="nexus"
    )
    service = MagicMock()
    service.inspect_image = AsyncMock(return_value=ImageInspection(digest="sha256:" + "a" * 64))
    with (
        patch("app.api.registry_source.HarborProfileService", return_value=profiles),
        patch("app.api.registry_source.NexusService", return_value=service),
    ):
        result = await inspect_exact_source(
            ExactSourceInspectionRequest(
                profile_id="nexus-profile", repository="team/app", reference="v1"
            ),
            request,
            MagicMock(),
            session,
        )
    assert result.digest == "sha256:" + "a" * 64
    assert result.repository == "team/app"
    assert service.inspect_image.await_count == 1


@pytest.mark.asyncio
async def test_exact_inspection_rejects_invalid_reference_before_network(context):
    request, session = context
    with patch("app.api.registry_source.NexusService") as nexus:
        with pytest.raises(HTTPException) as error:
            await inspect_exact_source(
                ExactSourceInspectionRequest(
                    profile_id="nexus-profile", repository="../private", reference="v1"
                ),
                request,
                MagicMock(),
                session,
            )
        assert error.value.status_code == 422
        nexus.assert_not_called()


@pytest.mark.asyncio
async def test_exact_inspection_rejects_harbor_profile(context):
    request, session = context
    profiles = MagicMock()
    profiles.operation_snapshot.return_value = SimpleNamespace(
        id="harbor-profile", registry_type="harbor"
    )
    with (
        patch("app.api.registry_source.HarborProfileService", return_value=profiles),
        patch("app.api.registry_source.NexusService") as nexus,
    ):
        with pytest.raises(HTTPException) as error:
            await inspect_exact_source(
                ExactSourceInspectionRequest(
                    profile_id="harbor-profile", repository="team/app", reference="v1"
                ),
                request,
                MagicMock(),
                session,
            )
        assert error.value.status_code == 409
        nexus.assert_not_called()


@pytest.mark.asyncio
async def test_nexus_helm_catalog_returns_typed_chart_versions(context):
    request, session = context
    service = MagicMock()
    service.list_charts = AsyncMock(return_value=HelmHttpResult(
        available=True,
        items=(HelmChartVersion("demo", "1.2.3", "demo-1.2.3.tgz", "a" * 64),),
    ))
    with patch("app.api.registry_source.NexusHelmHttpService", return_value=service):
        result = await nexus_helm_catalog(
            request, MagicMock(), session, "profile-a", "helm-hosted"
        )
    assert result.available
    assert [(x.name, x.version) for x in result.items] == [("demo", "1.2.3")]
    service.list_charts.assert_awaited_once_with("helm-hosted")


@pytest.mark.asyncio
async def test_nexus_helm_catalog_denied_is_not_misreported_as_empty(context):
    request, session = context
    service = MagicMock()
    service.list_charts = AsyncMock(return_value=HelmHttpResult(
        available=False, code="helm_repository_unavailable",
        message="Helm repository недоступен для чтения",
    ))
    with patch("app.api.registry_source.NexusHelmHttpService", return_value=service):
        result = await nexus_helm_catalog(
            request, MagicMock(), session, "profile-a", "helm-restricted"
        )
    assert not result.available
    assert result.items == []
    assert result.code == "helm_repository_unavailable"
