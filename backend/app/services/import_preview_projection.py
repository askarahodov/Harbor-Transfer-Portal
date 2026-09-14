from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from app.domain.imports import ImportIntakeMode
from app.schemas.imports import ImportPreviewResponse
from app.services.bundle_package_service import (
    BundlePackageService,
    BundleVerificationResult,
)
from app.services.import_orchestrator import ImportIntakeResult, ImportOrchestrator
from app.services.operation_manager import OperationTaskFailure


class _CapturingBundlePackageService:
    """Delegates package work while exposing the verified result to the projection layer."""

    def __init__(
        self,
        delegate: BundlePackageService,
        on_verified: Callable[[BundleVerificationResult], None],
    ) -> None:
        self._delegate = delegate
        self._on_verified = on_verified

    def verify_bundle(self, *args: Any, **kwargs: Any) -> BundleVerificationResult:
        result = self._delegate.verify_bundle(*args, **kwargs)
        self._on_verified(result)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ImportPreviewProjectionOrchestrator(ImportOrchestrator):
    """Adds verifier-derived UI fields without replacing the authoritative preview worker."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._verified_preview: BundleVerificationResult | None = None
        base_factory = self.package_factory

        def capturing_factory() -> BundlePackageService:
            service = _CapturingBundlePackageService(
                base_factory(),
                self._capture_verified_preview,
            )
            return cast(BundlePackageService, service)

        self.package_factory = capturing_factory

    async def discover_ready(
        self,
        *,
        actor_user_id: int,
        actor_username: str,
    ) -> tuple[ImportIntakeResult, ...]:
        # Browser upload and physical incoming are intentionally separate intake paths.
        # The base orchestrator historically reused import_max_upload_bytes for both;
        # discovery must instead accept archives up to the protocol archive limit.
        runtime_settings = self.settings
        self.settings = runtime_settings.model_copy(
            update={"import_max_upload_bytes": runtime_settings.bundle_max_archive_bytes}
        )
        try:
            return await super().discover_ready(
                actor_user_id=actor_user_id,
                actor_username=actor_username,
            )
        finally:
            self.settings = runtime_settings

    def _capture_verified_preview(self, result: BundleVerificationResult) -> None:
        self._verified_preview = result

    def _persist_preview(self, operation_id: int, preview: ImportPreviewResponse) -> None:
        verified = self._verified_preview
        if verified is None:
            raise OperationTaskFailure(
                "import_preview_projection_missing",
                "Verifier result отсутствует при сохранении import preview",
            )

        operation = self._get_import_operation(operation_id)
        intake_mode = None
        if operation.import_intake_mode is not None:
            intake_mode = ImportIntakeMode(operation.import_intake_mode)

        enriched = preview.model_copy(
            update={
                "bundle_filename": operation.bundle_filename,
                "intake_mode": intake_mode,
                "source_harbor": verified.manifest.source.harbor,
                "source_portal_version": verified.manifest.source.portal_version,
                "source_created_at": verified.manifest.created_at,
                "source_created_by": verified.manifest.created_by,
                "source_comment": verified.manifest.comment,
                "checksum_verified": True,
                "signature_verified": True,
                "schema_verified": True,
                "overwrite_allowed": self.settings.import_allow_overwrite,
            }
        )
        try:
            super()._persist_preview(operation_id, enriched)
        finally:
            self._verified_preview = None
