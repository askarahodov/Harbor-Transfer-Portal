from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.domain.bundle import ContainerImageArtifact, HelmChartArtifact
from app.domain.imports import ImportPreviewState
from app.schemas.imports import ImportArtifactPreviewResponse
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
    HelmTargetState,
)
from app.services.skopeo_service import (
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
    TargetState,
)

SkopeoFactory = Callable[[Session], SkopeoService]
HelmFactory = Callable[[Session], HelmOciService]


@dataclass(slots=True)
class ImportArtifactClassifier:
    session_factory: sessionmaker[Session]
    skopeo_factory: SkopeoFactory
    helm_factory: HelmFactory

    async def classify(
        self,
        artifacts: Sequence[ContainerImageArtifact | HelmChartArtifact],
        *,
        harbor_profile_id: str | None = None,
    ) -> list[ImportArtifactPreviewResponse]:
        result: list[ImportArtifactPreviewResponse] = []
        with self.session_factory() as session:
            skopeo = self.skopeo_factory(session)
            helm = self.helm_factory(session)
            if isinstance(skopeo, SkopeoService):
                skopeo.harbor_profile_id = harbor_profile_id
            if isinstance(helm, HelmOciService):
                helm.harbor_profile_id = harbor_profile_id
            for index, artifact in enumerate(artifacts):
                if isinstance(artifact, ContainerImageArtifact):
                    result.append(await self._classify_image(index, artifact, skopeo))
                else:
                    result.append(await self._classify_chart(index, artifact, helm))
        return result

    async def _classify_image(
        self,
        index: int,
        artifact: ContainerImageArtifact,
        skopeo: SkopeoService,
    ) -> ImportArtifactPreviewResponse:
        try:
            inspected = await skopeo.inspect_target(
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
        except (SkopeoServiceError, ValueError) as exc:
            return self._inspection_error(index, artifact, exc)

    async def _classify_chart(
        self,
        index: int,
        artifact: HelmChartArtifact,
        helm: HelmOciService,
    ) -> ImportArtifactPreviewResponse:
        try:
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
