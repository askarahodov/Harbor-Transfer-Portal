from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.domain.bundle import OperationStatus
from app.domain.imports import ImportIntakeMode
from app.schemas.imports import ImportPreviewResponse
from app.services.bundle_package_service import BundlePackageError
from app.services.import_orchestrator import ImportOrchestrator
from app.services.operation_manager import OperationContext, OperationTaskFailure


class ImportPreviewProjectionOrchestrator(ImportOrchestrator):
    """Import orchestration with a richer, verifier-derived preview projection for the UI."""

    async def _preview_worker(self, context: OperationContext, operation_id: int) -> None:
        context.transition(OperationStatus.VERIFYING)
        archive, sidecar = self._bundle_paths(operation_id)
        package_service = self.package_factory()
        try:
            verified = await asyncio.to_thread(
                package_service.verify_bundle,
                archive,
                sidecar_path=sidecar,
            )
        except BundlePackageError as exc:
            self._set_operation_error(operation_id, exc.code, exc.message)
            context.transition(OperationStatus.REJECTED)
            self._cleanup_storage(operation_id)
            return

        operation = self._get_import_operation(operation_id)
        if operation.bundle_sha256 is None or verified.archive_sha256 != operation.bundle_sha256:
            self._cleanup_storage(operation_id)
            raise OperationTaskFailure(
                "import_bundle_changed",
                "Bundle изменился между intake и verification",
            )

        intake_mode = None
        if operation.import_intake_mode is not None:
            intake_mode = ImportIntakeMode(operation.import_intake_mode)

        preview = ImportPreviewResponse(
            operation_id=operation_id,
            status=OperationStatus.READY,
            source_delivery_id=verified.manifest.delivery_id,
            bundle_sha256=verified.archive_sha256,
            bundle_size_bytes=verified.archive_size,
            signing_key_fingerprint=verified.signing_key_fingerprint,
            verified_at=datetime.now(UTC),
            bundle_filename=operation.bundle_filename,
            intake_mode=intake_mode,
            source_harbor=verified.manifest.source.harbor,
            source_portal_version=verified.manifest.source.portal_version,
            source_created_at=verified.manifest.created_at,
            source_created_by=verified.manifest.created_by,
            source_comment=verified.manifest.comment,
            checksum_verified=True,
            signature_verified=True,
            schema_verified=True,
            overwrite_allowed=self.settings.import_allow_overwrite,
            artifacts=await self._classify_manifest(verified.manifest.artifacts),
        )
        self._persist_preview(operation_id, preview)
        context.transition(OperationStatus.READY)
