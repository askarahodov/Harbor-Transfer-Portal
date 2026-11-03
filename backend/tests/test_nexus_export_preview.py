"""Nexus SOURCE preview must be type-aware, fail closed, and preserve Harbor behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_export_orchestrator import IMAGE_DIGEST, _environment

from app.domain.artifacts import ArtifactKind
from app.schemas.exports import ExportArtifactSelection
from app.services.export_orchestrator import ExportOrchestrationError, ExportOrchestrator
from app.services.harbor_profiles import HarborProfileService
from app.services.registry_client import ImageInspection


def _nexus_orchestrator(tmp_path: Path, digest: str = IMAGE_DIGEST):
    settings, manager, harbor, package_service, _ = _environment(tmp_path)
    with manager.session_factory() as session:
        profile = HarborProfileService(session, settings).create(
            name="Nexus", url="https://nexus.example.test",
            username="operator", verify_tls=True, enabled=True, registry_type="nexus",
        )
        session.commit()
    client = MagicMock()
    client.inspect_image = AsyncMock(return_value=ImageInspection(digest=digest))
    orchestrator = ExportOrchestrator(
        manager.session_factory, settings, manager,
        harbor_client_factory=lambda: harbor,
        registry_client_factory=lambda _session, _profile_id: client,
        package_factory=lambda: package_service,
    )
    return orchestrator, profile.id, harbor, client


def _image() -> ExportArtifactSelection:
    return ExportArtifactSelection(
        kind=ArtifactKind.CONTAINER_IMAGE, project="team", repository="app",
        reference="1.0.0", digest=IMAGE_DIGEST,
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
    orchestrator, profile_id, harbor, _client = _nexus_orchestrator(
        tmp_path, "sha256:" + "b" * 64
    )
    with pytest.raises(ExportOrchestrationError) as error:
        orchestrator.preview((_image(),), harbor_profile_id=profile_id)
    assert error.value.code == "export_source_changed"
    assert harbor.calls == {}


def test_nexus_helm_rejected_before_client_creation(tmp_path: Path) -> None:
    orchestrator, profile_id, harbor, client = _nexus_orchestrator(tmp_path)
    chart = ExportArtifactSelection(
        kind=ArtifactKind.HELM_CHART, project="team", repository="charts/sample",
        reference="1.2.3", digest=IMAGE_DIGEST,
    )
    with pytest.raises(ExportOrchestrationError) as error:
        orchestrator.preview((chart,), harbor_profile_id=profile_id)
    assert error.value.code == "nexus_legacy_helm_unsupported"
    client.inspect_image.assert_not_awaited()
    assert harbor.calls == {}


def test_nexus_prepare_reinspects_before_persisting(tmp_path: Path) -> None:
    orchestrator, profile_id, _harbor, client = _nexus_orchestrator(tmp_path)
    client.inspect_image.return_value = ImageInspection(digest="sha256:" + "c" * 64)
    with pytest.raises(ExportOrchestrationError) as error:
        orchestrator._prepare_export_operation(
            (_image(),), None, "operator", None, profile_id
        )
    assert error.value.code == "export_source_changed"
