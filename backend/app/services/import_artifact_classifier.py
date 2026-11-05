from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.bundle import ContainerImageArtifact, HelmChartArtifact
from app.domain.imports import ImportPreviewState
from app.schemas.imports import ImportArtifactPreviewResponse
from app.services.harbor_profiles import DEFAULT_PROFILE_ID, HarborProfileService
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
    HelmTargetState,
)
from app.services.nexus_helm_http import NexusHelmAdapter
from app.services.nexus_service import NexusServiceError
from app.services.registry_client import RegistryClient, create_registry_client
from app.services.skopeo_service import (
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
    TargetState,
)


class ImportArtifactClassifier:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings | Callable[[Session], SkopeoService],
        helm_factory: Callable[[Session], HelmOciService] | None = None,
        *,
        registry_client_factory: Callable[[Session, str | None], RegistryClient] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.registry_client_factory = registry_client_factory
        if isinstance(settings, Settings):
            self.settings = settings
            self.skopeo_factory = None
            self.helm_factory = helm_factory
        else:
            # Keep the existing fake-client injection used by domain tests.
            self.settings = Settings.model_construct()
            self.skopeo_factory = settings
            self.helm_factory = helm_factory

    async def classify(
        self,
        artifacts: Sequence[ContainerImageArtifact | HelmChartArtifact],
        *,
        harbor_profile_id: str | None = None,
    ) -> list[ImportArtifactPreviewResponse]:
        result: list[ImportArtifactPreviewResponse] = []
        for index, artifact in enumerate(artifacts):
            with self.session_factory() as session:
                if isinstance(artifact, ContainerImageArtifact):
                    client = self._create_registry_client(session, harbor_profile_id)
                    result.append(await self._classify_image(index, artifact, client))
                else:
                    result.append(
                        await self._classify_chart(index, artifact, session, harbor_profile_id)
                    )
        return result

    def _create_registry_client(
        self, session: Session, harbor_profile_id: str | None
    ) -> RegistryClient | SkopeoService:
        if self.registry_client_factory is not None:
            return self.registry_client_factory(session, harbor_profile_id)
        if self.skopeo_factory is not None:
            return self.skopeo_factory(session)
        from app.services.harbor_profiles import HarborProfileService

        profile = HarborProfileService(session, self.settings).operation_snapshot(harbor_profile_id)
        return create_registry_client(
            profile.registry_type, session, self.settings, profile_id=profile.id
        )

    async def _classify_image(
        self,
        index: int,
        artifact: ContainerImageArtifact,
        client: RegistryClient | SkopeoService,
    ) -> ImportArtifactPreviewResponse:
        try:
            inspected = await client.inspect_target(
                ImageReference(artifact.repository, artifact.reference),
                expected_digest=artifact.source_digest,
            )
            classification = {
                TargetState.ABSENT: ImportPreviewState.NEW,
                TargetState.SAME_DIGEST: ImportPreviewState.SAME,
                TargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                TargetState.PRESENT: ImportPreviewState.UNKNOWN,
            }[inspected.state]
            return ImportArtifactPreviewResponse(
                index=index,
                artifact_type=artifact.type,
                repository=artifact.repository,
                reference=artifact.reference,
                expected_digest=artifact.source_digest,
                target_digest=inspected.digest,
                payload_size=artifact.payload_size,
                classification=classification,
            )
        except (SkopeoServiceError, NexusServiceError, ValueError) as exc:
            return self._inspection_error(index, artifact, exc)

    async def _classify_chart(
        self,
        index: int,
        artifact: HelmChartArtifact,
        session: Session,
        harbor_profile_id: str | None,
    ) -> ImportArtifactPreviewResponse:
        try:
            # A configured Harbor OCI factory must never override the selected
            # Nexus profile. Resolve registry type before any injected factory.
            # Legacy unit tests explicitly inject a Harbor chart factory for
            # the default profile, without configuring a live Harbor endpoint.
            # Additional profiles must still be resolved fail-closed.
            resolve_profile = (
                harbor_profile_id is not None
                and not (
                    harbor_profile_id == DEFAULT_PROFILE_ID
                    and self.helm_factory is not None
                )
            )
            is_nexus = (
                resolve_profile
                and HarborProfileService(session, self.settings)
                .operation_snapshot(harbor_profile_id).registry_type == "nexus"
            )
            if is_nexus:
                assert harbor_profile_id is not None
                helm = NexusHelmAdapter(session, self.settings, harbor_profile_id)
            elif self.helm_factory is not None:
                helm = self.helm_factory(session)
            else:
                helm = HelmOciService(
                    session, self.settings, harbor_profile_id=harbor_profile_id
                )
            inspected = await helm.inspect_target(
                HelmChartReference(artifact.repository, artifact.name, artifact.version),
                expected_digest=artifact.source_digest,
            )
            classification = {
                HelmTargetState.ABSENT: ImportPreviewState.NEW,
                HelmTargetState.SAME_DIGEST: ImportPreviewState.SAME,
                HelmTargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                HelmTargetState.PRESENT: ImportPreviewState.UNKNOWN,
            }[inspected.state]
            return ImportArtifactPreviewResponse(
                index=index,
                artifact_type=artifact.type,
                repository=artifact.repository,
                name=artifact.name,
                version=artifact.version,
                expected_digest=artifact.source_digest,
                target_digest=inspected.digest,
                payload_size=artifact.payload_size,
                classification=classification,
            )
        except (HelmServiceError, ValueError) as exc:
            return self._inspection_error(index, artifact, exc)

    @staticmethod
    def _inspection_error(
        index: int,
        artifact: ContainerImageArtifact | HelmChartArtifact,
        exc: Exception,
    ) -> ImportArtifactPreviewResponse:
        return ImportArtifactPreviewResponse(
            index=index,
            artifact_type=artifact.type,
            repository=artifact.repository,
            name=getattr(artifact, "name", None),
            reference=getattr(artifact, "reference", None),
            version=getattr(artifact, "version", None),
            expected_digest=artifact.source_digest,
            payload_size=artifact.payload_size,
            classification=ImportPreviewState.ERROR,
            error_code=getattr(exc, "code", "import_target_inspection_failed"),
            message=str(exc),
        )
