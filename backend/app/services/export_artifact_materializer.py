from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.artifacts import ArtifactKind
from app.services.bundle_package_service import (
    ContainerImagePackageInput,
    HelmChartPackageInput,
    PackageArtifactInput,
)
from app.services.export_selection import ResolvedExportArtifact
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
)
from app.services.operation_manager import OperationTaskFailure
from app.services.skopeo_service import ImageReference, SkopeoService, SkopeoServiceError

SkopeoFactory = Callable[[Session], SkopeoService]
HelmFactory = Callable[[Session], HelmOciService]


class ExportArtifactMaterializer:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        skopeo_factory: SkopeoFactory | None = None,
        helm_factory: HelmFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.skopeo_factory = skopeo_factory
        self.helm_factory = helm_factory

    async def materialize(
        self,
        item: ResolvedExportArtifact,
        *,
        artifact_id: int,
        operation_workspace: Path,
        helm_root: Path,
    ) -> PackageArtifactInput:
        if item.kind is ArtifactKind.CONTAINER_IMAGE:
            image = ImageReference(
                repository=item.full_repository,
                reference=item.reference,
            )
            destination = operation_workspace / "images" / str(artifact_id)
            with self.session_factory() as session:
                result = await self._skopeo_service(session).export_image(image, destination)
            if result.source_digest != item.digest:
                raise SkopeoServiceError(
                    "export_source_changed",
                    "SOURCE artifact digest изменился после validation",
                )
            return ContainerImagePackageInput(
                repository=item.full_repository,
                reference=item.reference,
                source_digest=item.digest,
                source_path=result.payload_path,
                payload_path=f"images/{artifact_id}",
            )

        chart = self._helm_reference(item)
        destination = helm_root / str(artifact_id)
        with self.session_factory() as session:
            result = await self._helm_service(session).pull_chart(chart, destination)
        if result.source_digest != item.digest:
            raise HelmServiceError(
                "export_source_changed",
                "SOURCE Helm digest изменился после validation",
            )
        return HelmChartPackageInput(
            repository=chart.repository,
            name=chart.name,
            version=chart.version,
            source_digest=item.digest,
            source_path=result.package.path,
            payload_path=f"charts/{artifact_id}.tgz",
        )

    def prepare_helm_root(self, operation_id: int) -> Path:
        workspace_root = self.settings.helm_workspace_root.resolve()
        target = (
            workspace_root / "export-operations" / f"operation-{operation_id}"
        ).resolve()
        try:
            target.relative_to(workspace_root)
        except ValueError as exc:
            raise OperationTaskFailure(
                "export_workspace_invalid",
                "Helm export workspace вышел за разрешённый root",
            ) from exc
        if target.exists():
            if target.is_symlink() or not target.is_dir():
                raise OperationTaskFailure(
                    "export_workspace_unsafe",
                    "Helm export workspace имеет небезопасный тип",
                )
            shutil.rmtree(target)
        target.mkdir(parents=True, mode=0o700)
        return target

    @staticmethod
    def cleanup_helm_root(path: Path) -> None:
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def _skopeo_service(self, session: Session) -> SkopeoService:
        if self.skopeo_factory is not None:
            return self.skopeo_factory(session)
        return SkopeoService(session, self.settings)

    def _helm_service(self, session: Session) -> HelmOciService:
        if self.helm_factory is not None:
            return self.helm_factory(session)
        return HelmOciService(session, self.settings)

    @staticmethod
    def _helm_reference(item: ResolvedExportArtifact) -> HelmChartReference:
        parts = item.full_repository.split("/")
        if len(parts) < 2:
            raise HelmServiceError(
                "export_helm_repository_invalid",
                "Helm repository должен включать project и chart name",
            )
        return HelmChartReference(
            repository="/".join(parts[:-1]),
            name=parts[-1],
            version=item.reference,
        )
