from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ArtifactResult
from app.domain.bundle import ArtifactStatus
from app.schemas.imports import ImportReceiptResponse
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmCommandRunner,
    HelmOciService,
    HelmPhase,
    HelmProgressEvent,
    HelmPushResult,
    HelmServiceError,
    HelmTargetInspection,
    HelmTargetState,
)


class ImportHelmOciService(HelmOciService):
    """Helm OCI adapter for verified TARGET import payloads and overwrite policy.

    Re-pushing the same signed chart package can produce a different OCI manifest
    digest in TARGET. Successful imports therefore persist the verified
    SOURCE->TARGET digest pair. Replay is SAME only while the currently observed
    TARGET digest still matches that verified pair; external replacement remains
    a conflict.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        profile_id: str | None = None,
        runner: HelmCommandRunner | None = None,
        progress: Callable[[HelmProgressEvent], None] | None = None,
        digest_resolver: Callable[[HelmChartReference], str | None] | None = None,
    ) -> None:
        super().__init__(
            session,
            settings,
            profile_id=profile_id,
            runner=runner,
            progress=progress,
            digest_resolver=digest_resolver,
        )
        self._import_session = session

    def _validate_package_path(self, path: Path) -> Path:
        if path.is_symlink():
            raise HelmServiceError(
                "helm_package_invalid",
                "Ожидается обычный .tgz chart package без symlink",
            )

        resolved = path.resolve()
        verified_bundle_root = self.settings.bundle_extract_root.resolve()
        try:
            resolved.relative_to(verified_bundle_root)
        except ValueError:
            return super()._validate_package_path(path)

        if not resolved.is_file() or resolved.suffix != ".tgz":
            raise HelmServiceError(
                "helm_package_invalid",
                "Ожидается существующий .tgz chart package внутри verified bundle root",
            )
        return resolved

    async def inspect_target(
        self,
        chart: HelmChartReference,
        *,
        expected_digest: str | None = None,
    ) -> HelmTargetInspection:
        inspected = await super().inspect_target(chart, expected_digest=expected_digest)
        if (
            expected_digest is None
            or inspected.state is not HelmTargetState.CONFLICTING_DIGEST
            or inspected.digest is None
        ):
            return inspected

        if not self._verified_digest_pair_matches_target(
            chart,
            source_digest=expected_digest,
            target_digest=inspected.digest,
        ):
            return inspected
        return HelmTargetInspection(HelmTargetState.SAME_DIGEST, inspected.digest)

    def _verified_digest_pair_matches_target(
        self,
        chart: HelmChartReference,
        *,
        source_digest: str,
        target_digest: str,
    ) -> bool:
        candidates = self._import_session.scalars(
            select(ArtifactResult).where(
                ArtifactResult.artifact_type == "helm-chart",
                ArtifactResult.name == chart.name,
                ArtifactResult.version == chart.version,
                ArtifactResult.source_digest == source_digest,
                ArtifactResult.target_digest == target_digest,
                ArtifactResult.status == ArtifactStatus.VERIFIED,
            )
        ).all()
        for candidate in candidates:
            # Legacy/identity imports stored the same repository coordinate directly.
            if candidate.repository == chart.repository:
                return True

            # Destination mapping keeps ArtifactResult.repository as SOURCE provenance.
            # Bind the verified digest pair to the actual mapped TARGET path recorded in
            # the immutable receipt so a pair from another project cannot authorize replay.
            receipt_json = candidate.operation.import_receipt_json
            if receipt_json is None:
                continue
            try:
                receipt = ImportReceiptResponse.model_validate_json(receipt_json)
            except ValueError:
                continue
            if any(
                item.artifact_type == "helm-chart"
                and item.repository == candidate.repository
                and item.name == chart.name
                and item.version == chart.version
                and item.expected_digest == source_digest
                and item.target_digest == target_digest
                and item.target_repository == chart.repository
                and item.status == ArtifactStatus.VERIFIED
                for item in receipt.artifacts
            ):
                return True
        return False

    async def push_chart(
        self,
        package: Path,
        target: HelmChartReference,
        *,
        source_digest: str | None = None,
        allow_existing: bool = False,
    ) -> HelmPushResult:
        if source_digest is not None:
            self._validate_optional_digest(source_digest)
        preflight = await self.inspect_target(target, expected_digest=source_digest)
        if preflight.state is not HelmTargetState.ABSENT and not allow_existing:
            raise HelmServiceError(
                "helm_target_exists",
                "TARGET уже содержит chart/version; решение конфликта выполняет import engine",
            )

        package_path = self._validate_package_path(package)
        harbor = self.harbor_settings.resolve(self.harbor_profile_id)
        registry = self._registry_host(harbor)
        with self._security_context(harbor) as security:
            package_metadata = await self._validate_package(package_path, target, security)
            await self._login(registry, harbor, security, target)
            self._emit(HelmPhase.PUSHING, target)
            argv = (
                self.settings.helm_binary,
                "push",
                str(package_path),
                self._repository_oci_reference(registry, target),
                *self._tls_flags(harbor, security),
            )
            await self._run_checked(argv, security)

        self._emit(HelmPhase.VERIFYING_TARGET, target)
        target_digest = await asyncio.to_thread(self.digest_resolver, target)
        if target_digest is None:
            raise HelmServiceError(
                "helm_target_not_visible",
                "Harbor не подтвердил Helm artifact после успешного helm push",
            )
        self._validate_optional_digest(target_digest)
        self._emit(HelmPhase.PUSHED, target)
        return HelmPushResult(
            package=package_metadata,
            target_digest=target_digest,
            source_digest=source_digest,
            digest_matches_source=(
                target_digest == source_digest if source_digest is not None else None
            ),
        )
