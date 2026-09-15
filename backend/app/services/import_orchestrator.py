from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import PortalContour, Settings
from app.db.models import ArtifactResult, Operation
from app.domain.bundle import (
    ArtifactStatus,
    ContainerImageArtifact,
    HelmChartArtifact,
    OperationStatus,
    OperationType,
)
from app.domain.imports import ImportIntakeMode, ImportPreviewState
from app.schemas.imports import (
    ImportArtifactPreviewResponse,
    ImportPreviewResponse,
    ImportReceiptArtifactResponse,
    ImportReceiptResponse,
    ImportRetryResponse,
)
from app.services.bundle_package_service import BundlePackageError, BundlePackageService
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
    HelmTargetState,
)
from app.services.operation_manager import (
    OperationContext,
    OperationManager,
    OperationManagerError,
    OperationTaskFailure,
)
from app.services.skopeo_service import (
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
    TargetState,
)


@dataclass(slots=True)
class ImportOrchestrationError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ImportIntakeResult:
    operation_id: int
    status: OperationStatus
    intake_mode: ImportIntakeMode


PackageFactory = Callable[[], BundlePackageService]
SkopeoFactory = Callable[[Session], SkopeoService]
HelmFactory = Callable[[Session], HelmOciService]


class ImportOrchestrator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        operation_manager: OperationManager,
        *,
        package_factory: PackageFactory | None = None,
        skopeo_factory: SkopeoFactory | None = None,
        helm_factory: HelmFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.operation_manager = operation_manager
        self.package_factory = package_factory or (lambda: BundlePackageService(settings))
        self.skopeo_factory = skopeo_factory or (lambda session: SkopeoService(session, settings))
        self.helm_factory = helm_factory or (lambda session: HelmOciService(session, settings))
        self.discovery_root = settings.import_discovery_root.resolve()
        self.staging_root = settings.import_staging_root.resolve()
        self.receipt_root = settings.import_receipt_root.resolve()
        self.extract_root = settings.bundle_extract_root.resolve()

    async def accept_upload(
        self,
        stream: AsyncIterator[bytes],
        *,
        content_length: int | None,
        actor_user_id: int,
        actor_username: str,
    ) -> ImportIntakeResult:
        self._require_target()
        if content_length is not None:
            if content_length < 1:
                raise ImportOrchestrationError("import_upload_empty", "Upload не содержит bundle")
            if content_length > self.settings.import_max_upload_bytes:
                raise ImportOrchestrationError(
                    "import_upload_too_large",
                    "Размер upload превышает допустимый лимит",
                )
        self._require_disk(content_length or 0)

        storage_key = secrets.token_hex(24)
        storage_dir = self._prepare_storage_dir(storage_key)
        temporary = storage_dir / "bundle.htp.tar.gz.part"
        archive = storage_dir / "bundle.htp.tar.gz"
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                async for chunk in stream:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.settings.import_max_upload_bytes:
                        raise ImportOrchestrationError(
                            "import_upload_too_large",
                            "Размер upload превышает допустимый лимит",
                        )
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if total == 0:
                raise ImportOrchestrationError("import_upload_empty", "Upload не содержит bundle")
            os.replace(temporary, archive)
            self._fsync_directory(storage_dir)
            operation_id = self._create_intake_operation(
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                status=OperationStatus.UPLOADED,
                mode=ImportIntakeMode.UPLOAD,
                storage_key=storage_key,
                filename=archive.name,
                sha256=digest.hexdigest(),
                size_bytes=total,
            )
        except Exception:
            shutil.rmtree(storage_dir, ignore_errors=True)
            raise

        self._submit_preview(operation_id)
        return ImportIntakeResult(operation_id, OperationStatus.UPLOADED, ImportIntakeMode.UPLOAD)

    async def discover_ready(
        self,
        *,
        actor_user_id: int,
        actor_username: str,
    ) -> tuple[ImportIntakeResult, ...]:
        self._require_target()
        self.discovery_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        ready: list[ImportIntakeResult] = []
        for archive in sorted(self.discovery_root.glob("*.htp.tar.gz")):
            sidecar = archive.with_name(archive.name + ".sha256")
            if (
                archive.is_symlink()
                or not archive.is_file()
                or sidecar.is_symlink()
                or not sidecar.is_file()
            ):
                continue
            size = archive.stat().st_size
            if size < 1 or size > self.settings.import_max_upload_bytes:
                continue
            self._require_disk(size)

            storage_key = secrets.token_hex(24)
            storage_dir = self._prepare_storage_dir(storage_key)
            claimed_archive = storage_dir / archive.name
            claimed_sidecar = storage_dir / sidecar.name
            try:
                os.replace(archive, claimed_archive)
                try:
                    os.replace(sidecar, claimed_sidecar)
                except Exception:
                    os.replace(claimed_archive, archive)
                    raise
                self._fsync_directory(storage_dir)
                sha256 = await asyncio.to_thread(self._sha256_file, claimed_archive)
                operation_id = self._create_intake_operation(
                    actor_user_id=actor_user_id,
                    actor_username=actor_username,
                    status=OperationStatus.DISCOVERED,
                    mode=ImportIntakeMode.INCOMING,
                    storage_key=storage_key,
                    filename=claimed_archive.name,
                    sha256=sha256,
                    size_bytes=size,
                )
            except Exception:
                shutil.rmtree(storage_dir, ignore_errors=True)
                raise
            self._submit_preview(operation_id)
            ready.append(
                ImportIntakeResult(
                    operation_id,
                    OperationStatus.DISCOVERED,
                    ImportIntakeMode.INCOMING,
                )
            )
        return tuple(ready)

    def preview(self, operation_id: int) -> ImportPreviewResponse:
        self._require_target()
        operation = self._get_import_operation(operation_id)
        if operation.import_preview_json is None:
            raise ImportOrchestrationError(
                "import_preview_not_ready",
                "Verified preview ещё не готов",
            )
        return ImportPreviewResponse.model_validate_json(operation.import_preview_json)

    def receipt(self, operation_id: int) -> ImportReceiptResponse:
        self._require_target()
        operation = self._get_import_operation(operation_id)
        if operation.import_receipt_json is None:
            raise ImportOrchestrationError(
                "import_receipt_not_ready",
                "Receipt ещё не сформирован",
            )
        return ImportReceiptResponse.model_validate_json(operation.import_receipt_json)

    async def start_import(
        self,
        operation_id: int,
        *,
        actor_username: str,
        overwrite_conflicts: bool,
    ) -> None:
        self._require_target()
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Import-операция не найдена",
                )
            if operation.status is not OperationStatus.READY:
                raise ImportOrchestrationError(
                    "import_not_ready",
                    "Import можно запускать только после verified preview",
                )
            if operation.import_preview_json is None or operation.bundle_sha256 is None:
                raise ImportOrchestrationError(
                    "import_preview_not_ready",
                    "Verified preview отсутствует",
                )
            preview = ImportPreviewResponse.model_validate_json(operation.import_preview_json)
            unresolved = [
                item
                for item in preview.artifacts
                if item.classification in {ImportPreviewState.UNKNOWN, ImportPreviewState.ERROR}
            ]
            if unresolved:
                raise ImportOrchestrationError(
                    "import_preview_unresolved",
                    "Preview содержит UNKNOWN/ERROR; mutation Harbor запрещена",
                )
            conflicts = [
                item
                for item in preview.artifacts
                if item.classification is ImportPreviewState.CONFLICT
            ]
            if conflicts and not overwrite_conflicts:
                raise ImportOrchestrationError(
                    "import_conflict_blocked",
                    "Preview содержит CONFLICT; overwrite по умолчанию запрещён",
                )
            if overwrite_conflicts and not self.settings.import_allow_overwrite:
                raise ImportOrchestrationError(
                    "import_overwrite_disabled",
                    "Overwrite запрещён server-side policy",
                )
            operation.import_policy_json = json.dumps(
                {
                    "bundle_sha256": operation.bundle_sha256,
                    "overwrite_conflicts": overwrite_conflicts,
                    "requested_by": actor_username,
                    "requested_at": datetime.now(UTC).isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            session.commit()
        try:
            self.operation_manager.submit(
                operation_id,
                lambda context: self._import_worker(context, operation_id),
            )
        except OperationManagerError as exc:
            raise ImportOrchestrationError(exc.code, exc.message) from exc

    async def retry_import(
        self,
        original_operation_id: int,
        actor_username: str,
        overwrite_conflicts: bool,
    ) -> ImportRetryResponse:
        self._require_target()
        with self.session_factory() as session:
            original = session.get(Operation, original_operation_id)
            if original is None or original.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Исходная операция не найдена",
                )
            if original.status not in {
                OperationStatus.FAILED,
                OperationStatus.COMPLETED,
                OperationStatus.CANCELLED,
            }:
                raise ImportOrchestrationError(
                    "import_not_retryable",
                    "Повторный запуск возможен только для терминальной операции",
                )
            if original.import_preview_json is None or original.bundle_sha256 is None:
                raise ImportOrchestrationError(
                    "import_preview_not_ready",
                    "Verified preview отсутствует у исходной операции",
                )
            if original.retry_of_operation_id is not None:
                raise ImportOrchestrationError(
                    "import_already_retry",
                    "Эта операция уже является retry",
                )
            preview = ImportPreviewResponse.model_validate_json(original.import_preview_json)

        # Create new operation linked to the original
        new_operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username=actor_username,
            actor_user_id=original.actor_user_id,
            delivery_id=original.delivery_id,
            runtime_mode=original.runtime_mode,
            runtime_mode_version=original.runtime_mode_version,
            retry_of_operation_id=original_operation_id,
            destination_plan_id=original.destination_plan_id,
            destination_plan_hash=original.destination_plan_hash,
            failure_policy=original.failure_policy or "continue",
            comment=f"retry of operation {original_operation_id}",
        )
        with self.session_factory() as session:
            session.add(new_operation)
            session.flush()
            new_operation_id = new_operation.id
            session.commit()

        # Persist preview and artifacts for the new operation
        operation_context = OperationContext(
            self.operation_manager, new_operation_id, secrets.token_hex(16)
        )
        self._persist_preview(new_operation_id, preview)

        # Copy artifact source/target mapping from original artifacts
        with self.session_factory() as session:
            original_artifacts = session.scalars(
                select(ArtifactResult).where(ArtifactResult.operation_id == original_operation_id)
            ).all()
            for orig_artifact in original_artifacts:
                retry_artifact = ArtifactResult(
                    operation_id=new_operation_id,
                    artifact_type=orig_artifact.artifact_type,
                    repository=orig_artifact.repository,
                    name=orig_artifact.name,
                    reference=orig_artifact.reference,
                    version=orig_artifact.version,
                    source_digest=orig_artifact.source_digest,
                    source_project=orig_artifact.source_project,
                    source_repository=orig_artifact.source_repository,
                    source_reference=orig_artifact.source_reference,
                    target_project=orig_artifact.target_project,
                    target_repository=orig_artifact.target_repository,
                    target_reference=orig_artifact.target_reference,
                    destination_plan_id=orig_artifact.destination_plan_id,
                    status=ArtifactStatus.PENDING,
                )
                session.add(retry_artifact)
            session.commit()

        operation_context.transition(OperationStatus.READY)
        return ImportRetryResponse(
            operation_id=new_operation_id,
            retry_of_operation_id=original_operation_id,
            status=OperationStatus.READY,
            destination_plan_id=original.destination_plan_id,
            artifact_count=len(preview.artifacts),
            message=(
                f"Retry создан как операция #{new_operation_id}, "
                f"связанная с исходной #{original_operation_id}"
            ),
        )

    def _submit_preview(self, operation_id: int) -> None:
        try:
            self.operation_manager.submit(
                operation_id,
                lambda context: self._preview_worker(context, operation_id),
            )
        except OperationManagerError as exc:
            raise ImportOrchestrationError(exc.code, exc.message) from exc

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

        preview = ImportPreviewResponse(
            operation_id=operation_id,
            status=OperationStatus.READY,
            source_delivery_id=verified.manifest.delivery_id,
            bundle_sha256=verified.archive_sha256,
            bundle_size_bytes=verified.archive_size,
            signing_key_fingerprint=verified.signing_key_fingerprint,
            verified_at=datetime.now(UTC),
            artifacts=await self._classify_manifest(verified.manifest.artifacts),
        )
        self._persist_preview(operation_id, preview)
        context.transition(OperationStatus.READY)

    async def _classify_manifest(
        self,
        artifacts: Sequence[ContainerImageArtifact | HelmChartArtifact],
    ) -> list[ImportArtifactPreviewResponse]:
        result: list[ImportArtifactPreviewResponse] = []
        with self.session_factory() as session:
            skopeo = self.skopeo_factory(session)
            helm = self.helm_factory(session)
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

    async def _import_worker(self, context: OperationContext, operation_id: int) -> None:
        context.transition(OperationStatus.IMPORTING)
        (
            operation,
            preview,
            overwrite,
            requested_at,
            is_retry,
            original_operation_id,
        ) = self._load_execution_state(operation_id)
        archive, sidecar = self._bundle_paths(operation_id)
        observed_sha = await asyncio.to_thread(self._sha256_file, archive)
        if observed_sha != operation.bundle_sha256 or observed_sha != preview.bundle_sha256:
            raise OperationTaskFailure(
                "import_bundle_changed",
                "Bundle изменился после preview; import отменён до mutation Harbor",
            )

        extraction = self.extract_root / f"import-{operation_id}"
        self._remove_path(extraction)
        try:
            verified = await asyncio.to_thread(
                self.package_factory().verify_bundle,
                archive,
                sidecar_path=sidecar,
                extract_to=extraction,
            )
        except BundlePackageError as exc:
            raise OperationTaskFailure(exc.code, exc.message) from exc
        if (
            verified.archive_sha256 != preview.bundle_sha256
            or verified.manifest.delivery_id != preview.source_delivery_id
        ):
            raise OperationTaskFailure(
                "import_bundle_changed",
                "Bundle identity не совпадает с verified preview",
            )
        if verified.extracted_root is None:
            raise OperationTaskFailure(
                "import_extract_failed",
                "Verified bundle не был извлечён",
            )

        artifacts = operation.artifacts
        if len(artifacts) != len(verified.manifest.artifacts):
            raise OperationTaskFailure(
                "import_preview_artifacts_changed",
                "Состав operation artifacts не совпадает с manifest",
            )

        # Build original artifact results map for retry revalidation
        original_results: dict[int, ArtifactResult] = {}
        if is_retry and original_operation_id is not None:
            with self.session_factory() as session:
                original_artifacts = session.scalars(
                    select(ArtifactResult).where(
                        ArtifactResult.operation_id == original_operation_id
                    )
                ).all()
                for orig in original_artifacts:
                    original_results[orig.id] = orig

        failures = 0
        context.set_progress(current=0, total=len(artifacts))
        with self.session_factory() as session:
            skopeo = self.skopeo_factory(session)
            helm = self.helm_factory(session)
            pairs = zip(artifacts, verified.manifest.artifacts, strict=True)
            for index, (row, descriptor) in enumerate(pairs):
                context.raise_if_cancelled()

                # Retry revalidation: check if artifact was already imported
                if is_retry and row.id in original_results:
                    orig_result = original_results[row.id]
                    if orig_result.status in {
                        ArtifactStatus.IMPORTED,
                        ArtifactStatus.SKIPPED,
                    }:
                        # Revalidate target state
                        try:
                            outcome = await self._preflight_target(descriptor, skopeo, helm)
                        except (SkopeoServiceError, HelmServiceError, ValueError):
                            outcome = (ImportPreviewState.UNKNOWN, None)

                        if outcome[0] is ImportPreviewState.SAME:
                            context.set_artifact_status(
                                row.id,
                                ArtifactStatus.SKIPPED,
                                target_digest=outcome[1],
                            )
                            context.set_progress(current=index + 1, total=len(artifacts))
                            continue
                        elif outcome[0] is ImportPreviewState.CONFLICT:
                            context.set_artifact_status(
                                row.id,
                                ArtifactStatus.CONFLICT,
                                target_digest=outcome[1],
                            )
                            failures += 1
                            context.set_progress(current=index + 1, total=len(artifacts))
                            continue

                try:
                    outcome = await self._preflight_target(descriptor, skopeo, helm)
                except (SkopeoServiceError, HelmServiceError, ValueError) as exc:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.FAILED,
                        error_code=getattr(exc, "code", "import_target_inspection_failed"),
                        error_message=str(exc),
                    )
                    failures += 1
                    context.set_progress(current=index + 1, total=len(artifacts))
                    continue

                if outcome[0] is ImportPreviewState.SAME:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.SKIPPED,
                        target_digest=outcome[1],
                    )
                    context.set_progress(current=index + 1, total=len(artifacts))
                    continue
                if outcome[0] is ImportPreviewState.CONFLICT and not overwrite:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.CONFLICT,
                        target_digest=outcome[1],
                    )
                    failures += 1
                    context.set_progress(current=index + 1, total=len(artifacts))
                    continue
                if outcome[0] in {ImportPreviewState.UNKNOWN, ImportPreviewState.ERROR}:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.FAILED,
                        error_code="import_target_state_unresolved",
                        error_message="TARGET state нельзя безопасно разрешить перед mutation",
                    )
                    failures += 1
                    context.set_progress(current=index + 1, total=len(artifacts))
                    continue

                context.set_artifact_status(row.id, ArtifactStatus.RUNNING)
                payload = verified.extracted_root.joinpath(
                    *PurePosixPath(descriptor.payload_path).parts
                )
                try:
                    if isinstance(descriptor, ContainerImageArtifact):
                        imported = await skopeo.import_image(
                            payload,
                            ImageReference(descriptor.repository, descriptor.reference),
                            expected_digest=descriptor.source_digest,
                        )
                        target_digest = imported.target_digest
                    else:
                        pushed = await helm.push_chart(
                            payload,
                            HelmChartReference(
                                descriptor.repository,
                                descriptor.name,
                                descriptor.version,
                            ),
                            source_digest=descriptor.source_digest,
                            allow_existing=(
                                overwrite and outcome[0] is ImportPreviewState.CONFLICT
                            ),
                        )
                        if pushed.package.sha256 != descriptor.payload_sha256:
                            raise HelmServiceError(
                                "helm_payload_digest_mismatch",
                                "Helm package SHA-256 не совпадает с signed bundle payload",
                            )
                        target_digest = pushed.target_digest
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.VERIFIED,
                        target_digest=target_digest,
                    )
                except (SkopeoServiceError, HelmServiceError, ValueError) as exc:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.FAILED,
                        error_code=getattr(exc, "code", "import_artifact_failed"),
                        error_message=str(exc),
                    )
                    failures += 1
                context.set_progress(current=index + 1, total=len(artifacts))

        context.transition(OperationStatus.VERIFYING_TARGET)
        self._write_receipt(operation_id, preview, overwrite, requested_at, failures, is_retry)
        if failures:
            raise OperationTaskFailure(
                "import_partial_failure",
                "Import завершён с ошибками отдельных артефактов; rollback не выполнялся",
            )
        context.transition(OperationStatus.COMPLETED)

    async def _preflight_target(
        self,
        descriptor: ContainerImageArtifact | HelmChartArtifact,
        skopeo: SkopeoService,
        helm: HelmOciService,
    ) -> tuple[ImportPreviewState, str | None]:
        if isinstance(descriptor, ContainerImageArtifact):
            inspected = await skopeo.inspect_target(
                ImageReference(descriptor.repository, descriptor.reference),
                expected_digest=descriptor.source_digest,
            )
            classification = {
                TargetState.ABSENT: ImportPreviewState.NEW,
                TargetState.SAME_DIGEST: ImportPreviewState.SAME,
                TargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                TargetState.PRESENT: ImportPreviewState.UNKNOWN,
            }[inspected.state]
            return classification, inspected.digest
        inspected = await helm.inspect_target(
            HelmChartReference(descriptor.repository, descriptor.name, descriptor.version),
            expected_digest=descriptor.source_digest,
        )
        classification = {
            HelmTargetState.ABSENT: ImportPreviewState.NEW,
            HelmTargetState.SAME_DIGEST: ImportPreviewState.SAME,
            HelmTargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
            HelmTargetState.PRESENT: ImportPreviewState.UNKNOWN,
        }[inspected.state]
        return classification, inspected.digest

    def _load_execution_state(
        self,
        operation_id: int,
    ) -> tuple[Operation, ImportPreviewResponse, bool, datetime, bool, int | None]:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if (
                operation is None
                or operation.type is not OperationType.IMPORT
                or operation.import_preview_json is None
                or operation.import_policy_json is None
                or operation.bundle_sha256 is None
            ):
                raise OperationTaskFailure(
                    "import_execution_state_invalid",
                    "Persisted import execution state неполон",
                )
            _ = operation.artifacts
            preview = ImportPreviewResponse.model_validate_json(operation.import_preview_json)
            policy = json.loads(operation.import_policy_json)
            requested_at = datetime.fromisoformat(policy["requested_at"])
            overwrite = bool(policy.get("overwrite_conflicts", False))
            is_retry = operation.retry_of_operation_id is not None
            original_operation_id = operation.retry_of_operation_id
            session.expunge(operation)
            for artifact in operation.artifacts:
                session.expunge(artifact)
            return operation, preview, overwrite, requested_at, is_retry, original_operation_id

    def _persist_preview(self, operation_id: int, preview: ImportPreviewResponse) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Import operation не найдена при сохранении preview",
                )
            if operation.artifacts:
                raise OperationTaskFailure(
                    "import_preview_already_persisted",
                    "Artifact preview уже был сохранён",
                )
            operation.source_delivery_id = preview.source_delivery_id
            operation.bundle_sha256 = preview.bundle_sha256
            operation.bundle_size_bytes = preview.bundle_size_bytes
            operation.bundle_signing_key_fingerprint = preview.signing_key_fingerprint
            operation.import_preview_json = preview.model_dump_json()
            for item in preview.artifacts:
                session.add(
                    ArtifactResult(
                        operation_id=operation.id,
                        artifact_type=item.artifact_type,
                        repository=item.repository,
                        name=item.name,
                        reference=item.reference,
                        version=item.version,
                        source_digest=item.expected_digest,
                        size_bytes=item.payload_size,
                        status=ArtifactStatus.PENDING,
                    )
                )
            operation.total_artifacts = len(preview.artifacts)
            operation.progress_total = len(preview.artifacts)
            operation.progress_current = 0
            session.commit()

    def _write_receipt(
        self,
        operation_id: int,
        preview: ImportPreviewResponse,
        overwrite: bool,
        requested_at: datetime,
        failures: int,
        is_retry: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Operation отсутствует при формировании receipt",
                )
            artifacts = sorted(operation.artifacts, key=lambda item: item.id)
            receipt = ImportReceiptResponse(
                operation_id=operation.id,
                source_delivery_id=preview.source_delivery_id,
                bundle_sha256=preview.bundle_sha256,
                actor_username=operation.actor_username,
                started_at=requested_at,
                finished_at=now,
                overwrite_conflicts=overwrite,
                destination_plan_id=operation.destination_plan_id,
                destination_plan_hash=operation.destination_plan_hash,
                retry_of_operation_id=operation.retry_of_operation_id,
                failure_policy=operation.failure_policy,
                result="FAILED" if failures else "COMPLETED",
                artifacts=[
                    ImportReceiptArtifactResponse(
                        index=index,
                        artifact_type=item.artifact_type,
                        repository=item.repository,
                        name=item.name,
                        reference=item.reference,
                        version=item.version,
                        expected_digest=item.source_digest,
                        target_digest=item.target_digest,
                        source_project=item.source_project,
                        source_repository=item.source_repository,
                        source_reference=item.source_reference,
                        source_digest=item.source_digest,
                        target_project=item.target_project,
                        target_repository=item.target_repository,
                        target_reference=item.target_reference,
                        artifact_kind=item.artifact_type,
                        result=item.status.value,
                        error_code=item.error_code,
                        error_message=item.error_message,
                        size_bytes=item.size_bytes,
                        destination_plan_id=item.destination_plan_id,
                        overwrite_decision=("overwrite" if overwrite else "deny"),
                    )
                    for index, item in enumerate(artifacts)
                ],
            )
            payload = receipt.model_dump_json(indent=2) + "\n"
            operation.import_receipt_json = receipt.model_dump_json()
            session.commit()

        self.receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.receipt_root / f"import-{operation_id}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o440)
            self._fsync_directory(self.receipt_root)
        except FileExistsError as exc:
            raise OperationTaskFailure(
                "import_receipt_exists",
                "Immutable import receipt уже существует",
            ) from exc

    def _create_intake_operation(
        self,
        *,
        actor_user_id: int,
        actor_username: str,
        status: OperationStatus,
        mode: ImportIntakeMode,
        storage_key: str,
        filename: str,
        sha256: str,
        size_bytes: int,
    ) -> int:
        operation_id = self.operation_manager.create_operation(
            operation_type=OperationType.IMPORT,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            initial_status=status,
        )
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise ImportOrchestrationError(
                    "import_operation_create_failed",
                    "Не удалось создать import operation",
                )
            operation.import_storage_key = storage_key
            operation.import_intake_mode = mode.value
            operation.bundle_filename = filename
            operation.bundle_sha256 = sha256
            operation.bundle_size_bytes = size_bytes
            session.commit()
        return operation_id

    def _bundle_paths(self, operation_id: int) -> tuple[Path, Path | None]:
        operation = self._get_import_operation(operation_id)
        key = operation.import_storage_key
        filename = operation.bundle_filename
        if key is None or filename is None:
            raise OperationTaskFailure(
                "import_bundle_missing",
                "Persisted bundle path metadata отсутствует",
            )
        if len(key) != 48 or any(ch not in "0123456789abcdef" for ch in key):
            raise OperationTaskFailure(
                "import_bundle_path_invalid",
                "Storage key import bundle некорректен",
            )
        if Path(filename).name != filename:
            raise OperationTaskFailure(
                "import_bundle_path_invalid",
                "Имя import bundle некорректно",
            )
        archive = (self.staging_root / key / filename).resolve()
        try:
            archive.relative_to(self.staging_root)
        except ValueError as exc:
            raise OperationTaskFailure(
                "import_bundle_path_invalid",
                "Import bundle находится вне staging root",
            ) from exc
        if archive.is_symlink() or not archive.is_file():
            raise OperationTaskFailure(
                "import_bundle_missing",
                "Import bundle отсутствует в staging",
            )
        if operation.import_intake_mode != ImportIntakeMode.INCOMING.value:
            return archive, None
        sidecar = archive.with_name(archive.name + ".sha256")
        if sidecar.is_symlink() or not sidecar.is_file():
            raise OperationTaskFailure(
                "import_sidecar_missing",
                "Incoming bundle не имеет readiness .sha256 sidecar",
            )
        return archive, sidecar

    def _get_import_operation(self, operation_id: int) -> Operation:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Import-операция не найдена",
                )
            _ = operation.artifacts
            session.expunge(operation)
            for artifact in operation.artifacts:
                session.expunge(artifact)
            return operation

    def _set_operation_error(self, operation_id: int, code: str, message: str) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is not None:
                operation.error_code = code
                operation.error_message = message
                session.commit()

    def _cleanup_storage(self, operation_id: int) -> None:
        try:
            operation = self._get_import_operation(operation_id)
        except ImportOrchestrationError:
            return
        key = operation.import_storage_key
        if key and len(key) == 48 and all(ch in "0123456789abcdef" for ch in key):
            shutil.rmtree(self.staging_root / key, ignore_errors=True)

    def _prepare_storage_dir(self, storage_key: str) -> Path:
        self.staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.staging_root, 0o700)
        storage_dir = self.staging_root / storage_key
        storage_dir.mkdir(mode=0o700)
        return storage_dir

    def _require_target(self) -> None:
        if self.settings.portal_contour is not PortalContour.TARGET:
            raise ImportOrchestrationError(
                "import_wrong_contour",
                "Import доступен только в контуре TARGET",
            )

    def _require_disk(self, expected_bytes: int) -> None:
        self.staging_root.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.staging_root.parent).free
        required = self.settings.operation_disk_reserve_bytes + max(expected_bytes, 0)
        if free < required:
            raise ImportOrchestrationError(
                "operation_insufficient_disk",
                "Недостаточно свободного места для import intake",
            )

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
