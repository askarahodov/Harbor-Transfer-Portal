"""Nexus SOURCE preview must be type-aware, fail closed, and preserve Harbor behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_export_orchestrator import IMAGE_DIGEST, _environment

from app.domain.artifacts import ArtifactKind
from app.schemas.exports import ExportArtifactSelection
from app.services.export_orchestrator import ExportOrchestrationError, ExportOrchestrator
from app.services.harbor_profiles import HarborProfileService
from app.services.nexus_helm_http import HelmChartVersion, HelmHttpResult
from app.services.registry_client import ImageInspection


def _nexus_orchestrator(tmp_path: Path, digest: str = IMAGE_DIGEST):
    settings, manager, harbor, package_service, _ = _environment(tmp_path)
    with manager.session_factory() as session:
        profile = HarborProfileService(session, settings).create(
            name="Nexus",
            url="https://nexus.example.test",
            username="operator",
            verify_tls=True,
            enabled=True,
            registry_type="nexus",
        )
        session.commit()
    client = MagicMock()
    client.inspect_image = AsyncMock(return_value=ImageInspection(digest=digest))
    orchestrator = ExportOrchestrator(
        manager.session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        registry_client_factory=lambda _session, _profile_id: client,
        package_factory=lambda: package_service,
    )
    return orchestrator, profile.id, harbor, client


def _image() -> ExportArtifactSelection:
    return ExportArtifactSelection(
        kind=ArtifactKind.CONTAINER_IMAGE,
        project="team",
        repository="app",
        reference="1.0.0",
        digest=IMAGE_DIGEST,
    )


def test_nexus_preview_uses_docker_inspection_without_harbor_api(tmp_path: Path) -> None:
    orchestrator, profile_id, harbor, client = _nexus_orchestrator(tmp_path)
    result = orchestrator.preview((_image(),), harbor_profile_id=profile_id)
    assert len(result) == 1
    assert result[0].digest == IMAGE_DIGEST
    assert result[0].size_bytes is None
    assert result[0].full_repository == "team/app"
    assert client.inspect_image.await_count == 1
    assert harbor.calls == {}


def test_nexus_changed_digest_rejected_before_operation(tmp_path: Path) -> None:
    orchestrator, profile_id, harbor, _client = _nexus_orchestrator(tmp_path, "sha256:" + "b" * 64)
    with pytest.raises(ExportOrchestrationError) as error:
        orchestrator.preview((_image(),), harbor_profile_id=profile_id)
    assert error.value.code == "export_source_changed"
    assert harbor.calls == {}


def test_nexus_helm_preview_uses_plain_http_catalog_without_harbor_api(tmp_path: Path) -> None:
    orchestrator, profile_id, harbor, client = _nexus_orchestrator(tmp_path)
    chart = ExportArtifactSelection(
        kind=ArtifactKind.HELM_CHART,
        project="helm-hosted",
        repository="sample",
        reference="1.2.3",
        digest=IMAGE_DIGEST,
    )
    adapter = MagicMock()
    adapter.client.list_charts = AsyncMock(return_value=HelmHttpResult(
        available=True,
        items=(HelmChartVersion("sample", "1.2.3", "sample-1.2.3.tgz", "a" * 64),),
    ))
    with patch("app.services.export_orchestrator.NexusHelmAdapter", return_value=adapter):
        result = orchestrator.preview((chart,), harbor_profile_id=profile_id)
    assert len(result) == 1
    assert result[0].kind is ArtifactKind.HELM_CHART
    assert result[0].full_repository == "helm-hosted/sample"
    assert result[0].digest == IMAGE_DIGEST
    adapter.client.list_charts.assert_awaited_once_with("helm-hosted")
    client.inspect_image.assert_not_awaited()
    assert harbor.calls == {}



def test_nexus_helm_only_preview_does_not_construct_docker_connector(
    tmp_path: Path,
) -> None:
    orchestrator, profile_id, harbor, client = _nexus_orchestrator(tmp_path)
    chart = ExportArtifactSelection(
        kind=ArtifactKind.HELM_CHART,
        project="helm-hosted",
        repository="sample",
        reference="1.2.3",
        digest=IMAGE_DIGEST,
    )
    adapter = MagicMock()
    adapter.client.list_charts = AsyncMock(return_value=HelmHttpResult(
        available=True,
        items=(HelmChartVersion("sample", "1.2.3", "sample-1.2.3.tgz", "a" * 64),),
    ))

    def unexpected_docker_client(_session, _profile_id):
        raise AssertionError("Helm HTTP-only preview must not initialize Docker")

    orchestrator._registry_client_factory = unexpected_docker_client
    with patch("app.services.export_orchestrator.NexusHelmAdapter", return_value=adapter):
        result = orchestrator.preview((chart,), harbor_profile_id=profile_id)
    assert len(result) == 1
    assert result[0].digest == IMAGE_DIGEST
    adapter.client.list_charts.assert_awaited_once_with("helm-hosted")
    client.inspect_image.assert_not_awaited()
    assert harbor.calls == {}


def test_nexus_mixed_preview_reuses_one_docker_client(
    tmp_path: Path,
) -> None:
    orchestrator, profile_id, harbor, client = _nexus_orchestrator(tmp_path)
    second = ExportArtifactSelection(
        kind=ArtifactKind.CONTAINER_IMAGE,
        project="team",
        repository="worker",
        reference="v2",
        digest=IMAGE_DIGEST,
    )
    created = []

    def docker_client(_session, selected_profile_id):
        created.append(selected_profile_id)
        return client

    orchestrator._registry_client_factory = docker_client
    result = orchestrator.preview((_image(), second), harbor_profile_id=profile_id)
    assert len(result) == 2
    assert created == [profile_id]
    assert client.inspect_image.await_count == 2
    assert [item.full_repository for item in result] == ["team/app", "team/worker"]
    assert harbor.calls == {}


def test_nexus_prepare_reinspects_before_persisting(tmp_path: Path) -> None:
    orchestrator, profile_id, _harbor, client = _nexus_orchestrator(tmp_path)
    client.inspect_image.return_value = ImageInspection(digest="sha256:" + "c" * 64)
    with pytest.raises(ExportOrchestrationError) as error:
        orchestrator._prepare_export_operation((_image(),), None, "operator", None, profile_id)
    assert error.value.code == "export_source_changed"


def test_nexus_repository_with_namespace_keeps_full_path(tmp_path: Path) -> None:
    """Registry namespace must not be dropped: project + repository is the Docker path."""
    orchestrator, profile_id, _harbor, client = _nexus_orchestrator(tmp_path)
    selection = ExportArtifactSelection(
        kind=ArtifactKind.CONTAINER_IMAGE,
        project="0702",
        repository="centos7",
        reference="kcptun",
        digest=IMAGE_DIGEST,
    )
    result = orchestrator.preview((selection,), harbor_profile_id=profile_id)
    assert result[0].full_repository == "0702/centos7"
    assert result[0].project is None
    inspected = client.inspect_image.await_args.args[0]
    assert inspected.repository == "0702/centos7"
