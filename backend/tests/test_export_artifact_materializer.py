from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import cast

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.domain.artifacts import ArtifactKind
from app.services.bundle_package_service import (
    ContainerImagePackageInput,
    HelmChartPackageInput,
)
from app.services.export_artifact_materializer import ExportArtifactMaterializer
from app.services.export_selection import ResolvedExportArtifact
from app.services.helm_oci_service import (
    HelmOciService,
    HelmPackageMetadata,
    HelmPullResult,
    HelmServiceError,
)
from app.services.operation_manager import OperationTaskFailure
from app.services.skopeo_service import ExportResult, SkopeoService, SkopeoServiceError

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
CHANGED_DIGEST = "sha256:" + "c" * 64


class FakeSkopeoService:
    def __init__(self, digest: str = IMAGE_DIGEST) -> None:
        self.digest = digest
        self.destination: Path | None = None

    async def export_image(self, _image, destination: Path) -> ExportResult:  # type: ignore[no-untyped-def]
        self.destination = destination
        destination.mkdir(parents=True)
        return ExportResult(
            source_digest=self.digest,
            payload_digest=self.digest,
            payload_path=destination,
        )


class FakeHelmService:
    def __init__(self, digest: str = CHART_DIGEST) -> None:
        self.digest = digest
        self.destination: Path | None = None

    async def pull_chart(self, chart, destination: Path) -> HelmPullResult:  # type: ignore[no-untyped-def]
        self.destination = destination
        destination.mkdir(parents=True)
        package = destination / f"{chart.name}-{chart.version}.tgz"
        package.write_bytes(b"chart")
        return HelmPullResult(
            source_digest=self.digest,
            package=HelmPackageMetadata(
                path=package,
                name=chart.name,
                version=chart.version,
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            ),
        )


def materializer(
    tmp_path: Path,
    *,
    skopeo: FakeSkopeoService | None = None,
    helm: FakeHelmService | None = None,
) -> ExportArtifactMaterializer:
    database_url = f"sqlite:///{tmp_path / 'materializer.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        helm_workspace_root=tmp_path / "helm",
    )
    skopeo_service = skopeo or FakeSkopeoService()
    helm_service = helm or FakeHelmService()
    return ExportArtifactMaterializer(
        session_factory,
        settings,
        skopeo_factory=lambda _session: cast(SkopeoService, skopeo_service),
        helm_factory=lambda _session: cast(HelmOciService, helm_service),
    )


def image_artifact() -> ResolvedExportArtifact:
    return ResolvedExportArtifact(
        kind=ArtifactKind.CONTAINER_IMAGE,
        project="team",
        repository="app",
        reference="1.0.0",
        digest=IMAGE_DIGEST,
        size_bytes=101,
    )


def chart_artifact() -> ResolvedExportArtifact:
    return ResolvedExportArtifact(
        kind=ArtifactKind.HELM_CHART,
        project="team",
        repository="charts/sample",
        reference="1.2.3",
        digest=CHART_DIGEST,
        size_bytes=202,
    )


def test_materializes_container_with_stable_bundle_path(tmp_path: Path) -> None:
    skopeo = FakeSkopeoService()
    service = materializer(tmp_path, skopeo=skopeo)
    workspace = tmp_path / "operation"
    helm_root = tmp_path / "helm-root"

    result = asyncio.run(
        service.materialize(
            image_artifact(),
            artifact_id=7,
            operation_workspace=workspace,
            helm_root=helm_root,
        )
    )

    assert isinstance(result, ContainerImagePackageInput)
    assert result.repository == "team/app"
    assert result.reference == "1.0.0"
    assert result.source_digest == IMAGE_DIGEST
    assert result.payload_path == "images/7"
    assert skopeo.destination == workspace / "images" / "7"
    assert result.source_path == skopeo.destination


def test_container_digest_drift_fails_closed(tmp_path: Path) -> None:
    service = materializer(tmp_path, skopeo=FakeSkopeoService(CHANGED_DIGEST))

    with pytest.raises(SkopeoServiceError) as captured:
        asyncio.run(
            service.materialize(
                image_artifact(),
                artifact_id=1,
                operation_workspace=tmp_path / "operation",
                helm_root=tmp_path / "helm-root",
            )
        )

    assert captured.value.code == "export_source_changed"


def test_materializes_helm_with_stable_bundle_path(tmp_path: Path) -> None:
    helm = FakeHelmService()
    service = materializer(tmp_path, helm=helm)
    helm_root = tmp_path / "helm-root"

    result = asyncio.run(
        service.materialize(
            chart_artifact(),
            artifact_id=9,
            operation_workspace=tmp_path / "operation",
            helm_root=helm_root,
        )
    )

    assert isinstance(result, HelmChartPackageInput)
    assert result.repository == "team/charts"
    assert result.name == "sample"
    assert result.version == "1.2.3"
    assert result.source_digest == CHART_DIGEST
    assert result.payload_path == "charts/9.tgz"
    assert helm.destination == helm_root / "9"
    assert result.source_path == helm.destination / "sample-1.2.3.tgz"


def test_helm_digest_drift_fails_closed(tmp_path: Path) -> None:
    service = materializer(tmp_path, helm=FakeHelmService(CHANGED_DIGEST))

    with pytest.raises(HelmServiceError) as captured:
        asyncio.run(
            service.materialize(
                chart_artifact(),
                artifact_id=2,
                operation_workspace=tmp_path / "operation",
                helm_root=tmp_path / "helm-root",
            )
        )

    assert captured.value.code == "export_source_changed"


def test_helm_workspace_rejects_unsafe_existing_path(tmp_path: Path) -> None:
    service = materializer(tmp_path)
    unsafe = tmp_path / "helm" / "export-operations" / "operation-5"
    unsafe.parent.mkdir(parents=True)
    unsafe.write_text("not-a-directory", encoding="utf-8")

    with pytest.raises(OperationTaskFailure) as captured:
        service.prepare_helm_root(5)

    assert captured.value.code == "export_workspace_unsafe"
