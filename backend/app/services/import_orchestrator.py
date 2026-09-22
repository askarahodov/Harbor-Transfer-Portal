from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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
    ImportPreviewResponse,
    ImportReceiptArtifactResponse,
    ImportReceiptResponse,
)
from app.services.bundle_package_service import BundlePackageError, BundlePackageService
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
    HelmTargetState,
)
from app.services.import_artifact_classifier import ImportArtifactClassifier
from app.services.import_bundle_storage import (
    ImportBundleStorageError,
    resolve_persisted_bundle_paths,
)
from app.services.key_management import KeyManagementError, KeyManagementService
from app.services.media_handoff import MediaHandoffError, MediaHandoffService
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
        self.skopeo_factory = skopeo_factory or (
            lambda session: SkopeoService(session, settings)
        )
        self.helm_factory = helm_factory or (
            lambda session: HelmOciService(session, settings)
        )
        self.artifact_classifier = ImportArtifactClassifier(
            session_factory,
            self.skopeo_factory,
            self.helm_factory,
        )
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
        bundle_filename: str | None = None,
        sidecar_payload: bytes | None = None,
        handoff_payload: bytes | None = None,
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
        browser_handoff = sidecar_payload is not None or handoff_payload is not None
        if browser_handoff and (sidecar_payload is None or handoff_payload is None):
            shutil.rmtree(storage_dir, ignore_errors=True)
            raise ImportOrchestrationError(
                "handoff_browser_files_incomplete",
                "Browser intake требует bundle, .sha256 и signed handoff вместе",
            )
        filename = bundle_filename or "bundle.htp.tar.gz"
        if Path(filename).name != filename or not filename.endswith(".htp.tar.gz"):
            shutil.rmtree(storage_dir, ignore_errors=True)
            raise ImportOrchestrationError(
                "import_bundle_filename_invalid",
                "Имя browser bundle некорректно",
            )
        temporary = storage_dir / f"{filename}.part"
        archive = storage_dir / filename
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

            if browser_handoff:
                sidecar = storage_dir / f"{filename}.sha256"
                delivery_id = filename.removesuffix(".htp.tar.gz")
                handoff = storage_dir / f"{delivery_id}.htp-handoff.json"
                self._write_browser_metadata(sidecar, sidecar_payload or b"")
                self._write_browser_metadata(handoff, handoff_payload or b"")
                try:
                    verified_handoff = MediaHandoffService(self.settings).verify_from_root(
                        handoff_payload or b"",
                        storage_dir,
                    )
                except MediaHandoffError as exc:
                    raise ImportOrchestrationError(exc.code, exc.message) from exc
                if verified_handoff.delivery_id != delivery_id:
                    raise ImportOrchestrationError(
                        "handoff_delivery_mismatch",
                        "Signed handoff относится к другому Delivery ID",
                    )

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
            delivery_id = archive.name.removesuffix(".htp.tar.gz")
            handoff = archive.with_name(f"{delivery_id}.htp-handoff.json")
            if (
                archive.is_symlink()
                or not archive.is_file()
                or sidecar.is_symlink()
                or not sidecar.is_file()
                or handoff.is_symlink()
                or not handoff.is_file()
            ):
                continue
            try:
                verified_handoff = MediaHandoffService(
                    self.settings
                ).verify_from_discovery(handoff.read_bytes())
            except (OSError, MediaHandoffError) as exc:
                if isinstance(exc, MediaHandoffError):
                    raise ImportOrchestrationError(exc.code, exc.message) from exc
                raise ImportOrchestrationError(
                    "handoff_file_invalid",
                    "Signed physical handoff не удалось прочитать",
                ) from exc
            if verified_handoff.delivery_id != delivery_id:
                raise ImportOrchestrationError(
                    "handoff_delivery_mismatch",
                    "Signed handoff относится к другому Delivery ID",
                )
            size = archive.stat().st_size
            if size < 1 or size > self.settings.import_max_upload_bytes:
                continue
            self._require_disk(size)

            storage_key = secrets.token_hex(24)
            storage_dir = self._prepare_storage_dir(storage_key)
            claimed_archive = storage_dir / archive.name
            claimed_sidecar = storage_dir / sidecar.name
            claimed_handoff = storage_dir / handoff.name
            try:
                os.replace(archive, claimed_archive)
                try:
                    os.replace(sidecar, claimed_sidecar)
                    os.replace(handoff, claimed_handoff)
                except Exception:
                    if claimed_sidecar.exists():
                        os.replace(claimed_sidecar, sidecar)
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
            self._require_preview_signer_trusted(preview)

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

    def _require_preview_signer_trusted(
        self,
        preview: ImportPreviewResponse,
    ) -> None:
        try:
            trusted = {
                item.fingerprint
                for item in KeyManagementService(self.settings).list_trusted_keys()
                if item.enabled
            }
        except KeyManagementError as exc:
            raise ImportOrchestrationError(exc.code, exc.message) from exc
        if preview.signing_key_fingerprint not in trusted:
            raise ImportOrchestrationError(
                "import_signing_key_untrusted",
                "SOURCE signing identity из verified preview больше не trusted/enabled",
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

        self._persist_verified_signer(
            operation_id,
            verified.signing_key_fingerprint,
        )

        preview = ImportPreviewResponse(
            operation_id=operation_id,
            status=OperationStatus.READY,
            source_delivery_id=verified.manifest.delivery_id,
            bundle_sha256=verified.archive_sha256,
            bundle_size_bytes=verified.archive_size,
            signing_key_fingerprint=verified.signing_key_fingerprint,
            verified_at=datetime.now(UTC),
            artifacts=await self.artifact_classifier.classify(verified.manifest.artifacts),
        )
        self._persist_preview(operation_id, preview)
        context.transition(OperationStatus.READY)

    async def _import_worker(self, context: OperationContext, operation_id: int) -> None:
        context.transition(OperationStatus.IMPORTING)
        operation, preview, overwrite, requested_at = self._load_execution_state(operation_id)
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

        failures = 0
        context.set_progress(current=0, total=len(artifacts))
        with self.session_factory() as session:
            skopeo = self.skopeo_factory(session)
            helm = self.helm_factory(session)
            pairs = zip(artifacts, verified.manifest.artifacts, strict=True)
            for index, (row, descriptor) in enumerate(pairs):
                context.raise_if_cancelled()
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
        self._write_receipt(operation_id, preview, overwrite, requested_at, failures)
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
    ) -> tuple[Operation, ImportPreviewResponse, bool, datetime]:
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
            session.expunge(operation)
            for artifact in operation.artifacts:
                session.expunge(artifact)
            return operation, preview, overwrite, requested_at

    def _persist_verified_signer(
        self,
        operation_id: int,
        fingerprint: str,
    ) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Import operation не найдена при сохранении verified signer",
                )
            existing = operation.bundle_signing_key_fingerprint
            if existing is not None and existing != fingerprint:
                raise OperationTaskFailure(
                    "import_signing_key_changed",
                    "Verified signer fingerprint изменился внутри import operation",
                )
            operation.bundle_signing_key_fingerprint = fingerprint
            session.commit()

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
                        status=item.status,
                        error_code=item.error_code,
                        error_message=item.error_message,
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
        try:
            return resolve_persisted_bundle_paths(
                self.staging_root,
                operation.import_storage_key,
                operation.bundle_filename,
                operation.import_intake_mode,
            )
        except ImportBundleStorageError as exc:
            raise OperationTaskFailure(exc.code, exc.message) from exc

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

    def _write_browser_metadata(self, path: Path, payload: bytes) -> None:
        if not payload or len(payload) > self.settings.bundle_max_metadata_bytes:
            raise ImportOrchestrationError(
                "handoff_size_invalid",
                "Browser handoff metadata пусты или превышают допустимый limit",
            )
        try:
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
        except OSError as exc:
            raise ImportOrchestrationError(
                "handoff_file_invalid",
                "Не удалось сохранить browser handoff metadata",
            ) from exc

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