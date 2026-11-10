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
from app.services.nexus_helm_http import NexusHelmAdapter
from app.services.operation_manager import OperationTaskFailure
from app.services.registry_client import RegistryClient, create_registry_client
from app.services.skopeo_service import ImageReference, SkopeoService, SkopeoServiceError


class ExportArtifactMaterializer:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        skopeo_factory: Callable[[Session], SkopeoService] | None = None,
        helm_factory: Callable[[Session], HelmOciService] | None = None,
        registry_client_factory: Callable[[Session, str | None], RegistryClient] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._skopeo_factory = skopeo_factory
        self._helm_factory = helm_factory
        self._registry_client_factory = registry_client_factory

    def _create_registry_client(
        self, session: Session, harbor_profile_id: str | None
    ) -> RegistryClient | SkopeoService:
        if self._registry_client_factory is not None:
            return self._registry_client_factory(session, harbor_profile_id)
        if self._skopeo_factory is not None:
            return self._skopeo_factory(session)
        from app.services.harbor_profiles import HarborProfileService

        profile = HarborProfileService(session, self.settings).operation_snapshot(harbor_profile_id)
        return create_registry_client(
            profile.registry_type, session, self.settings, profile_id=profile.id
        )

    async def materialize(
        self,
        item: ResolvedExportArtifact,
        *,
        artifact_id: int,
        operation_workspace: Path,
        helm_root: Path,
        harbor_profile_id: str | None = None,
    ) -> PackageArtifactInput:
        if item.kind is ArtifactKind.CONTAINER_IMAGE:
            image = ImageReference(
                repository=item.full_repository,
                reference=item.reference,
            )
            destination = operation_workspace / "images" / str(artifact_id)
            with self.session_factory() as session:
                client = self._create_registry_client(session, harbor_profile_id)
                result = await client.export_image(image, destination)
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
            helm = (
                self._helm_factory(session)
                if self._helm_factory is not None
                else self._helm_service(session, harbor_profile_id)
            )
            result = await helm.pull_chart(chart, destination)
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
        target = (workspace_root / "export-operations" / f"operation-{operation_id}").resolve()
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

    def _helm_service(
        self, session: Session, profile_id: str | None
    ) -> HelmOciService | NexusHelmAdapter:
        from app.services.harbor_profiles import HarborProfileService

        profile = HarborProfileService(session, self.settings).operation_snapshot(profile_id)
        if profile.registry_type == "nexus":
            return NexusHelmAdapter(session, self.settings, profile.id)
        return HelmOciService(session, self.settings, harbor_profile_id=profile.id)

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
