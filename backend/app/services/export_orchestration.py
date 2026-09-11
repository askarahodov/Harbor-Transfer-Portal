from __future__ import annotations

import asyncio
import errno
import os
import re
import secrets
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.config import PortalContour, Settings
from app.db.models import ArtifactResult, Operation, User, UserRole
from app.domain.bundle import ArtifactStatus, BundleSource, OperationStatus, OperationType
from app.schemas.exports import (
    ContainerImageSelection,
    ExportArtifactPreview,
    ExportPreviewResponse,
    ExportSelectionItem,
    ExportSelectionRequest,
    HelmChartSelection,
)
from app.services.bundle_package_service import (
    BundleBuildResult,
    BundlePackageError,
    BundlePackageService,
    ContainerImagePackageInput,
    HelmChartPackageInput,
    PackageArtifactInput,
)
from app.services.harbor_client import HarborArtifact, HarborClient, HarborClientError
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmPullResult,
    HelmServiceError,
)
from app.services.operation_manager import (
    OperationArtifactSpec,
    OperationContext,
    OperationHandle,
    OperationManager,
    OperationTaskFailure,
)
from app.services.skopeo_service import (
    ExportResult,
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
)

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_DELIVERY_RE = re.compile(r"^DELIVERY-[0-9]{8}-[A-Z0-9]{6,32}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_FINISHED_ARTIFACT_STATES = {
    ArtifactStatus.VERIFIED,
    ArtifactStatus.IMPORTED,
    ArtifactStatus.SKIPPED,
    ArtifactStatus.CONFLICT,
    ArtifactStatus.FAILED,
}
_EXPORTABLE_KINDS = {"container-image", "helm-chart"}


@dataclass(slots=True)
class ExportOrchestrationError(Exception):
    code: str
    message: str
    status_code: int = HTTPStatus.BAD_REQUEST

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ResolvedExportArtifact:
    kind: Literal["container-image", "helm-chart"]
    project: str
    harbor_repository: str
    repository: str
    source_digest: str
    size_bytes: int | None
    reference: str | None = None
    name: str | None = None
    version: str | None = None

    def preview(self) -> ExportArtifactPreview:
        return ExportArtifactPreview(
            type=self.kind,
            project=self.project,
            repository=self.harbor_repository,
            reference=self.reference,
            name=self.name,
            version=self.version,
            source_digest=self.source_digest,
            size_bytes=self.size_bytes,
        )


@dataclass(frozen=True, slots=True)
class PublishedBundle:
    operation_id: int
    delivery_id: str
    archive_path: Path
    sidecar_path: Path
    sha256: str
    size_bytes: int
    artifact_count: int

    @property
    def filename(self) -> str:
        return self.archive_path.name

    @property
    def checksum_filename(self) -> str:
        return self.sidecar_path.name


HarborClientFactory = Callable[[], HarborClient]
SkopeoFactory = Callable[[Session, Settings], SkopeoService]
HelmFactory = Callable[[Session, Settings], HelmOciService]
BundleFactory = Callable[[Settings], BundlePackageService]


class ExportOrchestrationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        operation_manager: OperationManager,
        *,
        harbor_client_factory: HarborClientFactory | None = None,
        skopeo_factory: SkopeoFactory | None = None,
        helm_factory: HelmFactory | None = None,
        bundle_factory: BundleFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.operation_manager = operation_manager
        self.harbor_client_factory = harbor_client_factory or self._default_harbor_client
        self.skopeo_factory = skopeo_factory or (
            lambda session, service_settings: SkopeoService(session, service_settings)
        )
        self.helm_factory = helm_factory or (
            lambda session, service_settings: HelmOciService(session, service_settings)
        )
        self.bundle_factory = bundle_factory or BundlePackageService

    async def preview(self, request: ExportSelectionRequest) -> ExportPreviewResponse:
        self._require_source_contour()
        resolved, source_harbor = await self._resolve_selection(
            request.artifacts,
            require_expected_digest=False,
        )
        known_size = sum(item.size_bytes or 0 for item in resolved)
        unknown_size = sum(item.size_bytes is None for item in resolved)
        return ExportPreviewResponse(
            source_harbor=source_harbor,
            artifacts=[item.preview() for item in resolved],
            known_size_bytes=known_size,
            unknown_size_count=unknown_size,
        )

    async def start(
        self,
        request: ExportSelectionRequest,
        actor: User,
    ) -> tuple[OperationHandle, str]:
        self._require_source_contour()
        resolved, source_harbor = await self._resolve_selection(
            request.artifacts,
            require_expected_digest=True,
        )
        delivery_id = self.bundle_factory(self.settings).allocate_delivery_id()
        specs = tuple(self._operation_spec(item) for item in resolved)
        worker = partial(
            self._worker,
            resolved=tuple(resolved),
            source_harbor=source_harbor,
            delivery_id=delivery_id,
            created_by=actor.username,
            comment=request.comment,
        )
        try:
            handle = self.operation_manager.create_and_submit(
                operation_type=OperationType.EXPORT,
                actor_user_id=actor.id,
                actor_username=actor.username,
                worker=worker,
                delivery_id=delivery_id,
                comment=request.comment,
                artifacts=specs,
            )
        except Exception as exc:
            if isinstance(exc, ExportOrchestrationError):
                raise
            raise ExportOrchestrationError(
                "export_start_failed",
                "Не удалось создать фоновую export-операцию",
                HTTPStatus.CONFLICT,
            ) from exc
        return handle, delivery_id

    def published_bundle(self, operation_id: int, actor: User) -> PublishedBundle:
        self._require_source_contour()
        operation = self.operation_manager.get_operation(operation_id)
        if operation is None or operation.type is not OperationType.EXPORT:
            raise ExportOrchestrationError(
                "export_operation_not_found",
                "Export-операция не найдена",
                HTTPStatus.NOT_FOUND,
            )
        self._authorize_bundle(operation, actor)
        if operation.status is not OperationStatus.COMPLETED:
            raise ExportOrchestrationError(
                "export_bundle_not_ready",
                "Bundle доступен только для завершённой export-операции",
                HTTPStatus.CONFLICT,
            )
        delivery_id = operation.delivery_id
        if delivery_id is None or _DELIVERY_RE.fullmatch(delivery_id) is None:
            raise ExportOrchestrationError(
                "export_delivery_invalid",
                "У export-операции отсутствует корректный delivery id",
                HTTPStatus.CONFLICT,
            )

        outgoing = self.settings.bundle_outgoing_root.resolve()
        archive = (outgoing / f"{delivery_id}.htp.tar.gz").resolve()
        sidecar = (outgoing / f"{delivery_id}.htp.tar.gz.sha256").resolve()
        self._require_regular_published_file(archive, outgoing)
        self._require_regular_published_file(sidecar, outgoing)
        digest = self._read_sidecar_digest(sidecar, archive.name)
        return PublishedBundle(
            operation_id=operation.id,
            delivery_id=delivery_id,
            archive_path=archive,
            sidecar_path=sidecar,
            sha256=digest,
            size_bytes=archive.stat().st_size,
            artifact_count=len(operation.artifacts),
        )

    async def _resolve_selection(
        self,
        selections: Sequence[ExportSelectionItem],
        *,
        require_expected_digest: bool,
    ) -> tuple[list[ResolvedExportArtifact], str]:
        self._reject_duplicates(selections)
        try:
            client = self.harbor_client_factory()
        except HarborSettingsError as exc:
            raise ExportOrchestrationError(
                exc.code,
                exc.message,
                HTTPStatus.SERVICE_UNAVAILABLE,
            ) from exc
        try:
            source_harbor = client.base_url
            resolved: list[ResolvedExportArtifact] = []
            for selection in selections:
                expected = selection.source_digest
                if require_expected_digest and expected is None:
                    raise ExportOrchestrationError(
                        "export_digest_required",
                        "Start export требует source_digest из validate/preview",
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                try:
                    artifact = await asyncio.to_thread(
                        client.get_artifact,
                        selection.project,
                        selection.repository,
                        self._selection_reference(selection),
                    )
                except HarborClientError as exc:
                    raise self._harbor_api_error(exc) from exc
                resolved.append(
                    self._resolve_artifact(
                        selection,
                        artifact,
                        expected_digest=expected,
                    )
                )
        finally:
            client.close()
        return resolved, source_harbor

    def _resolve_artifact(
        self,
        selection: ExportSelectionItem,
        artifact: HarborArtifact,
        *,
        expected_digest: str | None,
    ) -> ResolvedExportArtifact:
        digest = artifact.digest
        if _DIGEST_RE.fullmatch(digest) is None:
            raise ExportOrchestrationError(
                "export_source_digest_invalid",
                "Harbor вернул некорректный source digest",
                HTTPStatus.BAD_GATEWAY,
            )
        if expected_digest is not None and digest != expected_digest:
            raise ExportOrchestrationError(
                "export_source_changed",
                "Source artifact изменился после preview; повторите validation",
                HTTPStatus.CONFLICT,
            )
        if artifact.size is not None and artifact.size < 0:
            raise ExportOrchestrationError(
                "export_source_metadata_invalid",
                "Harbor вернул некорректный размер artifact",
                HTTPStatus.BAD_GATEWAY,
            )

        actual_kind = self._classify_harbor_artifact(artifact)
        if actual_kind not in _EXPORTABLE_KINDS:
            raise ExportOrchestrationError(
                "export_artifact_kind_unsupported",
                "Выбранный Harbor artifact не поддерживается export v1",
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if actual_kind != selection.type:
            raise ExportOrchestrationError(
                "export_artifact_kind_mismatch",
                "Тип выбранного artifact не совпадает с данными Harbor",
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )

        if isinstance(selection, ContainerImageSelection):
            return ResolvedExportArtifact(
                kind="container-image",
                project=selection.project,
                harbor_repository=selection.repository,
                repository=f"{selection.project}/{selection.repository}",
                reference=selection.reference,
                source_digest=digest,
                size_bytes=artifact.size,
            )

        service_repository, name = self._helm_coordinates(
            selection.project,
            selection.repository,
        )
        return ResolvedExportArtifact(
            kind="helm-chart",
            project=selection.project,
            harbor_repository=selection.repository,
            repository=service_repository,
            name=name,
            version=selection.version,
            source_digest=digest,
            size_bytes=artifact.size,
        )

    async def _worker(
        self,
        context: OperationContext,
        *,
        resolved: tuple[ResolvedExportArtifact, ...],
        source_harbor: str,
        delivery_id: str,
        created_by: str,
        comment: str | None,
    ) -> None:
        published = False
        final_archive, final_sidecar = self._final_paths(delivery_id)
        try:
            operation = self.operation_manager.get_operation(context.operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "export_operation_not_found",
                    "Export-операция не найдена",
                )
            artifact_rows = sorted(operation.artifacts, key=lambda item: item.id)
            if len(artifact_rows) != len(resolved):
                raise OperationTaskFailure(
                    "export_operation_artifacts_invalid",
                    "Persisted artifact list не совпадает с export selection",
                )

            context.transition(OperationStatus.VALIDATING)
            known_size = sum(item.size_bytes or 0 for item in resolved)
            context.require_disk(known_size)
            workspace = context.workspace()
            service_settings = self.settings.model_copy(
                update={
                    "skopeo_payload_root": workspace,
                    "helm_workspace_root": workspace,
                    "bundle_payload_root": workspace,
                    "bundle_temp_root": workspace / "bundle-temp",
                    "bundle_outgoing_root": workspace / "bundle-outgoing",
                }
            )
            try:
                current_harbor = self._effective_harbor_url()
            except HarborSettingsError as exc:
                raise OperationTaskFailure(exc.code, exc.message) from exc
            if current_harbor != source_harbor:
                raise OperationTaskFailure(
                    "export_harbor_changed",
                    "Конфигурация SOURCE Harbor изменилась после запуска export",
                )

            context.transition(OperationStatus.RUNNING)
            package_inputs: list[PackageArtifactInput] = []
            for index, (item, row) in enumerate(zip(resolved, artifact_rows, strict=True), start=1):
                context.raise_if_cancelled()
                context.set_artifact_status(row.id, ArtifactStatus.RUNNING)
                try:
                    await self._assert_source_unchanged(item)
                    package_input = await self._export_one(
                        item,
                        row,
                        index,
                        workspace,
                        service_settings,
                    )
                except (ExportOrchestrationError, SkopeoServiceError, HelmServiceError) as exc:
                    code = getattr(exc, "code", "export_artifact_failed")
                    message = getattr(exc, "message", "Не удалось экспортировать artifact")
                    self._mark_fail_fast(
                        context,
                        artifact_rows,
                        current_id=row.id,
                        code=code,
                        message=message,
                    )
                    raise OperationTaskFailure(code, message) from exc
                package_inputs.append(package_input)
                context.set_artifact_status(
                    row.id,
                    ArtifactStatus.VERIFIED,
                    size_bytes=item.size_bytes,
                )
                context.set_progress(current=index, total=len(resolved))

            context.raise_if_cancelled()
            context.transition(OperationStatus.PACKAGING)
            bundle_service = self.bundle_factory(service_settings)
            source = BundleSource(
                contour="SOURCE",
                harbor=source_harbor,
                portal_version=self.settings.app_version,
            )
            try:
                build = await self._run_blocking(
                    bundle_service.build_bundle,
                    source=source,
                    created_by=created_by,
                    artifacts=package_inputs,
                    delivery_id=delivery_id,
                    comment=comment,
                )
            except BundlePackageError as exc:
                raise OperationTaskFailure(exc.code, exc.message) from exc

            context.raise_if_cancelled()
            context.transition(OperationStatus.VERIFYING)
            staged_root = service_settings.bundle_outgoing_root.resolve()
            self._validate_build_result(build, delivery_id, staged_root)
            await self._run_blocking(
                self._publish_staged_bundle,
                build,
                final_archive,
                final_sidecar,
            )
            published = True
            context.raise_if_cancelled()
            context.cleanup_workspace()
            context.transition(OperationStatus.COMPLETED)
        except asyncio.CancelledError:
            if published:
                self._remove_published_delivery(final_archive, final_sidecar)
            raise
        except OperationTaskFailure:
            if published:
                self._remove_published_delivery(final_archive, final_sidecar)
            raise
        except Exception as exc:
            if published:
                self._remove_published_delivery(final_archive, final_sidecar)
            raise OperationTaskFailure(
                "export_orchestration_failed",
                "Export orchestration завершилась внутренней ошибкой",
            ) from exc

    async def _assert_source_unchanged(self, item: ResolvedExportArtifact) -> None:
        try:
            client = self.harbor_client_factory()
        except HarborSettingsError as exc:
            raise ExportOrchestrationError(
                exc.code,
                exc.message,
                HTTPStatus.SERVICE_UNAVAILABLE,
            ) from exc
        try:
            try:
                artifact = await asyncio.to_thread(
                    client.get_artifact,
                    item.project,
                    item.harbor_repository,
                    item.reference or item.version or "",
                )
            except HarborClientError as exc:
                raise self._harbor_api_error(exc) from exc
        finally:
            client.close()
        if artifact.digest != item.source_digest:
            raise ExportOrchestrationError(
                "export_source_changed",
                "Source artifact изменился после запуска export",
                HTTPStatus.CONFLICT,
            )

    async def _export_one(
        self,
        item: ResolvedExportArtifact,
        row: ArtifactResult,
        index: int,
        workspace: Path,
        service_settings: Settings,
    ) -> PackageArtifactInput:
        if item.kind == "container-image":
            if item.reference is None:
                raise ExportOrchestrationError(
                    "export_selection_invalid",
                    "Container image selection не содержит reference",
                )
            destination = workspace / "images" / f"artifact-{index:04d}"
            with self.session_factory() as session:
                service = self.skopeo_factory(session, service_settings)
                result: ExportResult = await service.export_image(
                    ImageReference(item.repository, item.reference),
                    destination,
                )
            if result.source_digest != item.source_digest:
                raise ExportOrchestrationError(
                    "export_source_changed",
                    "Image digest изменился между validation и Skopeo export",
                    HTTPStatus.CONFLICT,
                )
            return ContainerImagePackageInput(
                repository=item.repository,
                reference=item.reference,
                source_digest=item.source_digest,
                source_path=result.payload_path,
                payload_path=f"images/artifact-{index:04d}",
            )

        if item.name is None or item.version is None:
            raise ExportOrchestrationError(
                "export_selection_invalid",
                "Helm selection не содержит name/version",
            )
        destination = workspace / "charts" / f"artifact-{index:04d}"
        chart = HelmChartReference(
            repository=item.repository,
            name=item.name,
            version=item.version,
        )
        with self.session_factory() as session:
            service = self.helm_factory(session, service_settings)
            result: HelmPullResult = await service.pull_chart(chart, destination)
        if result.source_digest != item.source_digest:
            raise ExportOrchestrationError(
                "export_source_changed",
                "Helm digest изменился между validation и helm pull",
                HTTPStatus.CONFLICT,
            )
        return HelmChartPackageInput(
            repository=item.repository,
            name=item.name,
            version=item.version,
            source_digest=item.source_digest,
            source_path=result.package.path,
            payload_path=f"charts/artifact-{index:04d}.tgz",
        )

    def _mark_fail_fast(
        self,
        context: OperationContext,
        artifact_rows: Sequence[ArtifactResult],
        *,
        current_id: int,
        code: str,
        message: str,
    ) -> None:
        skip_remaining = False
        for row in artifact_rows:
            if row.id == current_id:
                context.set_artifact_status(
                    row.id,
                    ArtifactStatus.FAILED,
                    error_code=code,
                    error_message=message,
                )
                skip_remaining = True
            elif skip_remaining:
                context.set_artifact_status(
                    row.id,
                    ArtifactStatus.SKIPPED,
                    error_code="export_aborted_fail_fast",
                    error_message="Artifact не выполнялся из-за предыдущей ошибки export",
                )

    async def _run_blocking(self, func, /, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(partial(func, *args, **kwargs)))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)
            finally:
                raise

    def _validate_build_result(
        self,
        build: BundleBuildResult,
        delivery_id: str,
        staged_root: Path,
    ) -> None:
        archive = build.archive_path.resolve()
        sidecar = build.sidecar_path.resolve()
        self._require_regular_staged_file(archive, staged_root)
        self._require_regular_staged_file(sidecar, staged_root)
        if build.manifest.delivery_id != delivery_id:
            raise OperationTaskFailure(
                "export_delivery_mismatch",
                "Package service вернул другой delivery id",
            )
        if archive.name != f"{delivery_id}.htp.tar.gz":
            raise OperationTaskFailure(
                "export_bundle_filename_invalid",
                "Package service вернул неожидаемое имя bundle",
            )
        if sidecar.name != f"{archive.name}.sha256":
            raise OperationTaskFailure(
                "export_checksum_filename_invalid",
                "Package service вернул неожидаемое имя checksum sidecar",
            )
        if build.archive_size != archive.stat().st_size or build.archive_size <= 0:
            raise OperationTaskFailure(
                "export_bundle_size_invalid",
                "Размер staged bundle не совпадает с package metadata",
            )
        try:
            sidecar_digest = self._read_sidecar_digest(sidecar, archive.name)
        except ExportOrchestrationError as exc:
            raise OperationTaskFailure(exc.code, exc.message) from exc
        if build.archive_sha256 != sidecar_digest:
            raise OperationTaskFailure(
                "export_bundle_checksum_invalid",
                "Checksum staged bundle не совпадает с package metadata",
            )

    def _publish_staged_bundle(
        self,
        build: BundleBuildResult,
        final_archive: Path,
        final_sidecar: Path,
    ) -> None:
        outgoing = self.settings.bundle_outgoing_root.resolve()
        outgoing.mkdir(parents=True, exist_ok=True, mode=0o700)
        if final_archive.exists() or final_sidecar.exists():
            raise OperationTaskFailure(
                "export_delivery_exists",
                "Bundle с таким delivery id уже существует",
            )
        try:
            self._move_or_copy_atomic(build.archive_path, final_archive)
            self._move_or_copy_atomic(build.sidecar_path, final_sidecar)
        except Exception:
            final_sidecar.unlink(missing_ok=True)
            final_archive.unlink(missing_ok=True)
            raise

    @staticmethod
    def _move_or_copy_atomic(source: Path, destination: Path) -> None:
        source = source.resolve()
        destination = destination.resolve()
        try:
            os.replace(source, destination)
            os.chmod(destination, 0o600)
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise

        temporary = destination.parent / (
            f".{destination.name}.{secrets.token_hex(6)}.tmp"
        )
        try:
            with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            source.unlink()
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _remove_published_delivery(archive: Path, sidecar: Path) -> None:
        sidecar.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)

    def _final_paths(self, delivery_id: str) -> tuple[Path, Path]:
        outgoing = self.settings.bundle_outgoing_root.resolve()
        return (
            outgoing / f"{delivery_id}.htp.tar.gz",
            outgoing / f"{delivery_id}.htp.tar.gz.sha256",
        )

    def _read_sidecar_digest(self, sidecar: Path, archive_name: str) -> str:
        try:
            if sidecar.stat().st_size > self.settings.bundle_max_metadata_bytes:
                raise OSError("sidecar too large")
            text = sidecar.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ExportOrchestrationError(
                "export_checksum_invalid",
                "Checksum sidecar отсутствует или повреждён",
                HTTPStatus.CONFLICT,
            ) from exc
        suffix = f"  {archive_name}\n"
        if not text.endswith(suffix):
            raise ExportOrchestrationError(
                "export_checksum_invalid",
                "Checksum sidecar не соответствует bundle filename",
                HTTPStatus.CONFLICT,
            )
        digest = text[:64]
        if len(text) != 64 + len(suffix) or _SHA256_RE.fullmatch(digest) is None:
            raise ExportOrchestrationError(
                "export_checksum_invalid",
                "Checksum sidecar имеет неверный формат",
                HTTPStatus.CONFLICT,
            )
        return digest

    def _default_harbor_client(self) -> HarborClient:
        with self.session_factory() as session:
            return HarborSettingsService(session, self.settings).build_client()

    def _effective_harbor_url(self) -> str:
        with self.session_factory() as session:
            resolved = HarborSettingsService(session, self.settings).resolve()
        if not resolved.url:
            raise HarborSettingsError(
                "harbor_not_configured",
                "Локальный Harbor не настроен",
            )
        return resolved.url

    @staticmethod
    def _selection_reference(selection: ExportSelectionItem) -> str:
        if isinstance(selection, ContainerImageSelection):
            return selection.reference
        return selection.version

    @staticmethod
    def _helm_coordinates(project: str, harbor_repository: str) -> tuple[str, str]:
        parts = harbor_repository.split("/")
        name = parts[-1]
        parent = "/".join(parts[:-1])
        repository = f"{project}/{parent}" if parent else project
        return repository, name

    @staticmethod
    def _classify_harbor_artifact(
        artifact: HarborArtifact,
    ) -> Literal["container-image", "helm-chart", "unknown"]:
        artifact_type = (artifact.type or "").upper()
        haystack = " ".join(
            filter(None, [artifact.type, artifact.media_type])
        ).casefold()
        annotations = artifact.extra_attrs.get("annotations") if artifact.extra_attrs else None
        if isinstance(annotations, dict):
            haystack += " " + " ".join(
                str(value).casefold() for value in annotations.values()
            )
        if artifact_type in {"CHART", "HELM", "HELM_CHART"} or "helm" in haystack:
            return "helm-chart"
        if artifact_type == "IMAGE" or "image" in haystack or "container" in haystack:
            return "container-image"
        return "unknown"

    @staticmethod
    def _reject_duplicates(selections: Sequence[ExportSelectionItem]) -> None:
        seen: set[tuple[str, str, str, str]] = set()
        for selection in selections:
            reference = ExportOrchestrationService._selection_reference(selection)
            key = (
                selection.type,
                selection.project,
                selection.repository,
                reference,
            )
            if key in seen:
                raise ExportOrchestrationError(
                    "export_selection_duplicate",
                    "Export selection содержит duplicate artifact",
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            seen.add(key)

    @staticmethod
    def _harbor_api_error(exc: HarborClientError) -> ExportOrchestrationError:
        if exc.code == "not_found":
            return ExportOrchestrationError(
                "export_source_not_found",
                "Выбранный artifact не найден в локальном Harbor",
                HTTPStatus.NOT_FOUND,
            )
        if exc.code in {"unauthorized", "forbidden"}:
            return ExportOrchestrationError(
                "export_harbor_access_denied",
                "Локальный Harbor отклонил доступ портала",
                HTTPStatus.BAD_GATEWAY,
            )
        if exc.code in {
            "timeout",
            "connection_failed",
            "harbor_unavailable",
            "rate_limited",
            "tls_failed",
        }:
            return ExportOrchestrationError(
                "export_harbor_unavailable",
                "Локальный Harbor недоступен для export validation",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        return ExportOrchestrationError(
            "export_harbor_error",
            "Не удалось получить artifact metadata из локального Harbor",
            HTTPStatus.BAD_GATEWAY,
        )

    @staticmethod
    def _operation_spec(item: ResolvedExportArtifact) -> OperationArtifactSpec:
        return OperationArtifactSpec(
            artifact_type=item.kind,
            repository=item.repository,
            name=item.name,
            reference=item.reference,
            version=item.version,
            source_digest=item.source_digest,
            size_bytes=item.size_bytes,
        )

    def _require_source_contour(self) -> None:
        if self.settings.portal_contour is not PortalContour.SOURCE:
            raise ExportOrchestrationError(
                "export_wrong_contour",
                "Export workflow доступен только в контуре SOURCE",
                HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _authorize_bundle(operation: Operation, actor: User) -> None:
        if actor.role is UserRole.ADMIN:
            return
        if actor.role is UserRole.OPERATOR and operation.actor_user_id == actor.id:
            return
        raise ExportOrchestrationError(
            "export_bundle_forbidden",
            "Недостаточно прав для доступа к bundle этой операции",
            HTTPStatus.FORBIDDEN,
        )

    @staticmethod
    def _require_regular_published_file(path: Path, root: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ExportOrchestrationError(
                "export_bundle_path_invalid",
                "Bundle path находится вне configured outgoing directory",
                HTTPStatus.CONFLICT,
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise ExportOrchestrationError(
                "export_bundle_missing",
                "Published bundle file отсутствует",
                HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _require_regular_staged_file(path: Path, root: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise OperationTaskFailure(
                "export_staged_path_invalid",
                "Package service вернул файл вне private workspace",
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise OperationTaskFailure(
                "export_staged_file_invalid",
                "Package service не создал ожидаемый regular file",
            )