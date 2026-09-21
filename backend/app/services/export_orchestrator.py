from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import PortalContour, Settings
from app.db.models import Operation
from app.domain.artifacts import ArtifactKind, classify_artifact_kind
from app.domain.bundle import ArtifactStatus, BundleSource, OperationStatus, OperationType
from app.schemas.exports import ExportArtifactSelection
from app.services.bundle_package_service import (
    BundleBuildResult,
    BundlePackageError,
    BundlePackageService,
    ContainerImagePackageInput,
    HelmChartPackageInput,
    PackageArtifactInput,
)
from app.services.harbor_client import HarborClient, HarborClientError
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
)
from app.services.key_management import KeyManagementError, KeyManagementService
from app.services.operation_manager import (
    OperationArtifactSpec,
    OperationContext,
    OperationManager,
    OperationManagerError,
    OperationTaskFailure,
)
from app.services.skopeo_service import ImageReference, SkopeoService, SkopeoServiceError


@dataclass(slots=True)
class ExportOrchestrationError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ResolvedExportArtifact:
    kind: ArtifactKind
    project: str
    repository: str
    reference: str
    digest: str
    size_bytes: int | None

    @property
    def full_repository(self) -> str:
        return f"{self.project}/{self.repository}"


@dataclass(frozen=True, slots=True)
class ExportStartResult:
    operation_id: int
    delivery_id: str


@dataclass(frozen=True, slots=True)
class ExportBundleMetadata:
    operation_id: int
    delivery_id: str
    archive_path: Path
    archive_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExportHandoffMetadata:
    operation_id: int
    delivery_id: str
    handoff_path: Path
    handoff_size: int
    sha256: str
    signing_key_fingerprint: str


HarborClientFactory = Callable[[], HarborClient]
SkopeoFactory = Callable[[Session], SkopeoService]
HelmFactory = Callable[[Session], HelmOciService]
PackageFactory = Callable[[], BundlePackageService]


class ExportOrchestrator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        operation_manager: OperationManager,
        *,
        harbor_client_factory: HarborClientFactory | None = None,
        skopeo_factory: SkopeoFactory | None = None,
        helm_factory: HelmFactory | None = None,
        package_factory: PackageFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.operation_manager = operation_manager
        self.harbor_client_factory = harbor_client_factory
        self.skopeo_factory = skopeo_factory
        self.helm_factory = helm_factory
        self.package_factory = package_factory

    def preview(
        self,
        selections: Sequence[ExportArtifactSelection],
    ) -> tuple[ResolvedExportArtifact, ...]:
        self._require_source_contour()
        client = self._build_harbor_client()
        try:
            return tuple(
                self._resolve_selection(client, selection) for selection in selections
            )
        finally:
            client.close()

    async def start_export(
        self,
        selections: Sequence[ExportArtifactSelection],
        *,
        actor_user_id: int,
        actor_username: str,
        comment: str | None,
    ) -> ExportStartResult:
        self._require_source_contour()
        self._require_signing_identity()
        selection_snapshot = tuple(selections)
        resolved = await asyncio.to_thread(self.preview, selection_snapshot)
        estimated_bytes = sum(item.size_bytes or 0 for item in resolved)
        try:
            self.operation_manager.require_disk(estimated_bytes)
        except OperationTaskFailure as exc:
            raise ExportOrchestrationError(exc.code, exc.message) from exc

        delivery_id = self._package_service().allocate_delivery_id()
        operation_id = self.operation_manager.create_operation(
            operation_type=OperationType.EXPORT,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            comment=comment,
            delivery_id=delivery_id,
            artifacts=tuple(self._operation_spec(item) for item in resolved),
        )
        operation = self.operation_manager.get_operation(operation_id)
        if operation is None:
            raise ExportOrchestrationError(
                "export_operation_create_failed",
                "Не удалось загрузить созданную export-операцию",
            )
        ordered = sorted(operation.artifacts, key=lambda item: item.id)
        artifact_ids = tuple(artifact.id for artifact in ordered)
        if len(artifact_ids) != len(selection_snapshot):
            raise ExportOrchestrationError(
                "export_operation_create_failed",
                "Количество persisted artifacts не совпадает с export selection",
            )

        async def worker(context: OperationContext) -> None:
            await self._run_export(
                context,
                resolved=resolved,
                artifact_ids=artifact_ids,
                delivery_id=delivery_id,
                actor_username=actor_username,
                comment=comment,
            )

        try:
            self.operation_manager.submit(operation_id, worker)
        except OperationManagerError as exc:
            raise ExportOrchestrationError(exc.code, exc.message) from exc
        return ExportStartResult(operation_id=operation_id, delivery_id=delivery_id)

    def _require_signing_identity(self) -> None:
        try:
            signing = KeyManagementService(self.settings).signing_status()
        except KeyManagementError as exc:
            raise ExportOrchestrationError(exc.code, exc.message) from exc
        if not signing.configured:
            raise ExportOrchestrationError(
                "bundle_signing_key_not_configured",
                "SOURCE signing identity не настроена",
            )

    def bundle_metadata(self, operation_id: int) -> ExportBundleMetadata:
        operation = self.operation_manager.get_operation(operation_id)
        if operation is None or operation.type is not OperationType.EXPORT:
            raise ExportOrchestrationError(
                "export_operation_not_found",
                "Export-операция не найдена",
            )
        if operation.status is not OperationStatus.COMPLETED or not operation.delivery_id:
            raise ExportOrchestrationError(
                "export_not_ready",
                "Export bundle ещё не готов к выдаче",
            )
        if (
            operation.bundle_filename is None
            or operation.bundle_sha256 is None
            or operation.bundle_size_bytes is None
        ):
            raise ExportOrchestrationError(
                "export_bundle_metadata_invalid",
                "У завершённой export-операции отсутствует bundle metadata",
            )

        expected_name = f"{operation.delivery_id}.htp.tar.gz"
        if operation.bundle_filename != expected_name:
            raise ExportOrchestrationError(
                "export_bundle_metadata_invalid",
                "Имя export bundle не соответствует delivery_id",
            )
        archive, sidecar = self._delivery_paths(operation.delivery_id)
        if (
            not archive.is_file()
            or archive.is_symlink()
            or not sidecar.is_file()
            or sidecar.is_symlink()
        ):
            raise ExportOrchestrationError(
                "export_bundle_missing",
                "Готовый export bundle или его checksum sidecar отсутствует",
            )

        try:
            digest = self._sidecar_digest(sidecar, archive.name)
        except OperationTaskFailure as exc:
            raise ExportOrchestrationError(exc.code, exc.message) from exc
        archive_size = archive.stat().st_size
        if digest != operation.bundle_sha256 or archive_size != operation.bundle_size_bytes:
            raise ExportOrchestrationError(
                "export_bundle_metadata_invalid",
                "Файловая metadata export bundle не совпадает с persisted operation metadata",
            )
        return ExportBundleMetadata(
            operation_id=operation_id,
            delivery_id=operation.delivery_id,
            archive_path=archive,
            archive_size=archive_size,
            sha256=digest,
        )

    def handoff_metadata(self, operation_id: int) -> ExportHandoffMetadata:
        bundle = self.bundle_metadata(operation_id)
        operation = self.operation_manager.get_operation(operation_id)
        if operation is None or operation.type is not OperationType.EXPORT:
            raise ExportOrchestrationError(
                "export_operation_not_found",
                "Export-операция не найдена",
            )
        if (
            operation.handoff_filename is None
            or operation.handoff_sha256 is None
            or operation.handoff_size_bytes is None
            or operation.bundle_signing_key_fingerprint is None
        ):
            raise ExportOrchestrationError(
                "export_handoff_not_available",
                "Для этого export отсутствует signed physical handoff",
            )
        expected_name = f"{bundle.delivery_id}.htp-handoff.json"
        if operation.handoff_filename != expected_name:
            raise ExportOrchestrationError(
                "export_handoff_metadata_invalid",
                "Имя handoff не соответствует delivery_id",
            )
        handoff = (self.settings.bundle_outgoing_root.resolve() / expected_name).resolve()
        try:
            handoff.relative_to(self.settings.bundle_outgoing_root.resolve())
        except ValueError as exc:
            raise ExportOrchestrationError(
                "export_handoff_path_invalid",
                "Handoff path выходит за пределы outgoing storage",
            ) from exc
        if not handoff.is_file() or handoff.is_symlink():
            raise ExportOrchestrationError(
                "export_handoff_missing",
                "Signed physical handoff отсутствует",
            )
        size = handoff.stat().st_size
        digest = self._sha256_file(handoff)
        if size != operation.handoff_size_bytes or digest != operation.handoff_sha256:
            raise ExportOrchestrationError(
                "export_handoff_metadata_invalid",
                "Handoff file не совпадает с persisted operation metadata",
            )
        return ExportHandoffMetadata(
            operation_id=operation_id,
            delivery_id=bundle.delivery_id,
            handoff_path=handoff,
            handoff_size=size,
            sha256=digest,
            signing_key_fingerprint=operation.bundle_signing_key_fingerprint,
        )

    async def _run_export(
        self,
        context: OperationContext,
        *,
        resolved: tuple[ResolvedExportArtifact, ...],
        artifact_ids: tuple[int, ...],
        delivery_id: str,
        actor_username: str,
        comment: str | None,
    ) -> None:
        context.transition(OperationStatus.VALIDATING)
        try:
            context.require_disk(sum(item.size_bytes or 0 for item in resolved))
        except OperationTaskFailure as exc:
            self._fail_artifacts(context, artifact_ids, exc.code, exc.message)
            raise

        context.transition(OperationStatus.RUNNING)
        workspace = context.workspace()
        helm_root = self._prepare_helm_root(context.operation_id)
        package_inputs: list[PackageArtifactInput] = []
        materialized_artifact_ids: list[int] = []
        completed = False
        try:
            for index, (artifact_id, item) in enumerate(
                zip(artifact_ids, resolved, strict=True)
            ):
                context.raise_if_cancelled()
                context.set_artifact_status(artifact_id, ArtifactStatus.RUNNING)
                try:
                    package_input = await self._export_artifact(
                        item,
                        artifact_id=artifact_id,
                        operation_workspace=workspace,
                        helm_root=helm_root,
                    )
                except (SkopeoServiceError, HelmServiceError) as exc:
                    context.set_artifact_status(
                        artifact_id,
                        ArtifactStatus.FAILED,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    self._fail_artifacts(
                        context,
                        (*materialized_artifact_ids, *artifact_ids[index + 1 :]),
                        "export_aborted",
                        "Export bundle прерван после ошибки другого артефакта",
                    )
                    raise OperationTaskFailure(exc.code, exc.message) from exc
                package_inputs.append(package_input)
                materialized_artifact_ids.append(artifact_id)
                context.set_progress(current=index + 1, total=len(artifact_ids))

            context.transition(OperationStatus.PACKAGING)
            source = BundleSource(
                contour="SOURCE",
                harbor=self._effective_harbor_url(),
                portal_version=self.settings.app_version,
            )
            try:
                build = await self._build_bundle_cancellation_safe(
                    source=source,
                    created_by=actor_username,
                    artifacts=tuple(package_inputs),
                    delivery_id=delivery_id,
                    comment=comment,
                )
            except BundlePackageError as exc:
                self._cleanup_published_delivery(delivery_id)
                self._clear_bundle_metadata(context.operation_id)
                raise OperationTaskFailure(exc.code, exc.message) from exc

            try:
                context.raise_if_cancelled()
                context.transition(OperationStatus.VERIFYING)
                if build.manifest.delivery_id != delivery_id:
                    raise OperationTaskFailure(
                        "export_delivery_mismatch",
                        "Опубликованный bundle имеет неожиданный delivery_id",
                    )
                self._persist_bundle_metadata(context.operation_id, delivery_id, build)
                for artifact_id in artifact_ids:
                    context.set_artifact_status(artifact_id, ArtifactStatus.VERIFIED)
                context.set_progress(current=len(artifact_ids), total=len(artifact_ids))
                context.transition(OperationStatus.COMPLETED)
                completed = True
            except BaseException:
                self._clear_bundle_metadata(context.operation_id)
                self._cleanup_published_delivery(delivery_id)
                raise
        finally:
            self._cleanup_helm_root(helm_root)
            if completed:
                context.cleanup_workspace()

    async def _build_bundle_cancellation_safe(
        self,
        *,
        source: BundleSource,
        created_by: str,
        artifacts: tuple[PackageArtifactInput, ...],
        delivery_id: str,
        comment: str | None,
    ) -> BundleBuildResult:
        package_service = self._package_service()
        task = asyncio.create_task(
            asyncio.to_thread(
                package_service.build_bundle,
                source=source,
                created_by=created_by,
                artifacts=artifacts,
                delivery_id=delivery_id,
                comment=comment,
            )
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            self._cleanup_published_delivery(delivery_id)
            raise

    def _persist_bundle_metadata(
        self,
        operation_id: int,
        delivery_id: str,
        build: BundleBuildResult,
    ) -> None:
        expected_archive, expected_sidecar = self._delivery_paths(delivery_id)
        if (
            build.archive_path.resolve() != expected_archive
            or build.sidecar_path.resolve() != expected_sidecar
        ):
            raise OperationTaskFailure(
                "export_bundle_path_invalid",
                "BundlePackageService вернул путь вне ожидаемого delivery location",
            )
        sidecar_digest = self._sidecar_digest(expected_sidecar, expected_archive.name)
        if build.archive_sha256 != sidecar_digest:
            raise OperationTaskFailure(
                "export_bundle_metadata_invalid",
                "SHA-256 опубликованного bundle не совпадает с readiness sidecar",
            )
        if expected_archive.stat().st_size != build.archive_size:
            raise OperationTaskFailure(
                "export_bundle_metadata_invalid",
                "Размер опубликованного bundle изменился до фиксации metadata",
            )
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "export_operation_not_found",
                    "Export-операция исчезла до фиксации bundle metadata",
                )
            operation.bundle_filename = expected_archive.name
            operation.bundle_sha256 = build.archive_sha256
            operation.bundle_size_bytes = build.archive_size
            operation.handoff_filename = build.handoff_path.name
            operation.handoff_sha256 = build.handoff_sha256
            operation.handoff_size_bytes = build.handoff_size
            operation.bundle_signing_key_fingerprint = build.signing_key_fingerprint
            session.commit()

    def _clear_bundle_metadata(self, operation_id: int) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                return
            operation.bundle_filename = None
            operation.bundle_sha256 = None
            operation.bundle_size_bytes = None
            operation.handoff_filename = None
            operation.handoff_sha256 = None
            operation.handoff_size_bytes = None
            operation.bundle_signing_key_fingerprint = None
            session.commit()

    def _cleanup_published_delivery(self, delivery_id: str) -> None:
        archive, sidecar = self._delivery_paths(delivery_id)
        handoff = self.settings.bundle_outgoing_root.resolve() / (
            f"{delivery_id}.htp-handoff.json"
        )
        handoff.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)

    def _delivery_paths(self, delivery_id: str) -> tuple[Path, Path]:
        root = self.settings.bundle_outgoing_root.resolve()
        archive = (root / f"{delivery_id}.htp.tar.gz").resolve()
        sidecar = (root / f"{delivery_id}.htp.tar.gz.sha256").resolve()
        try:
            archive.relative_to(root)
            sidecar.relative_to(root)
        except ValueError as exc:
            raise OperationTaskFailure(
                "export_bundle_path_invalid",
                "Путь export bundle вышел за разрешённый outgoing root",
            ) from exc
        return archive, sidecar

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sidecar_digest(sidecar: Path, archive_name: str) -> str:
        try:
            text = sidecar.read_text(encoding="utf-8")
        except OSError as exc:
            raise OperationTaskFailure(
                "export_bundle_metadata_invalid",
                "Не удалось прочитать readiness sidecar export bundle",
            ) from exc
        suffix = f"  {archive_name}\n"
        if len(text) != 64 + len(suffix) or not text.endswith(suffix):
            raise OperationTaskFailure(
                "export_bundle_metadata_invalid",
                "Readiness sidecar export bundle имеет неверный формат",
            )
        digest = text[:64]
        if any(character not in "0123456789abcdef" for character in digest):
            raise OperationTaskFailure(
                "export_bundle_metadata_invalid",
                "Readiness sidecar содержит неверный SHA-256",
            )
        return digest

    async def _export_artifact(
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

    def _resolve_selection(
        self,
        client: HarborClient,
        selection: ExportArtifactSelection,
    ) -> ResolvedExportArtifact:
        try:
            artifact = client.get_artifact(
                selection.project,
                selection.repository,
                selection.reference,
            )
        except HarborClientError as exc:
            if exc.code == "not_found":
                raise ExportOrchestrationError(
                    "export_source_not_found",
                    "Выбранный SOURCE artifact больше не найден",
                ) from exc
            raise self._normalize_harbor_error(exc) from exc

        actual_kind = classify_artifact_kind(
            artifact.type,
            artifact.media_type,
            artifact.extra_attrs,
        )
        if actual_kind is ArtifactKind.UNKNOWN_OCI:
            raise ExportOrchestrationError(
                "export_artifact_unsupported",
                "Этот OCI artifact type не поддерживается export v1",
            )
        if actual_kind is not selection.kind:
            raise ExportOrchestrationError(
                "export_artifact_kind_changed",
                "Тип SOURCE artifact изменился после выбора",
            )
        if artifact.digest != selection.digest:
            raise ExportOrchestrationError(
                "export_source_changed",
                "SOURCE artifact digest изменился после выбора",
            )
        return ResolvedExportArtifact(
            kind=actual_kind,
            project=selection.project,
            repository=selection.repository,
            reference=selection.reference,
            digest=artifact.digest,
            size_bytes=artifact.size,
        )

    def _build_harbor_client(self) -> HarborClient:
        if self.harbor_client_factory is not None:
            return self.harbor_client_factory()
        try:
            with self.session_factory() as session:
                return HarborSettingsService(session, self.settings).build_client()
        except HarborSettingsError as exc:
            raise ExportOrchestrationError(exc.code, exc.message) from exc

    def _effective_harbor_url(self) -> str:
        try:
            with self.session_factory() as session:
                resolved = HarborSettingsService(session, self.settings).resolve()
        except HarborSettingsError as exc:
            raise ExportOrchestrationError(exc.code, exc.message) from exc
        if not resolved.url:
            raise ExportOrchestrationError(
                "harbor_not_configured",
                "Локальный Harbor не настроен",
            )
        return resolved.url

    def _skopeo_service(self, session: Session) -> SkopeoService:
        if self.skopeo_factory is not None:
            return self.skopeo_factory(session)
        return SkopeoService(session, self.settings)

    def _helm_service(self, session: Session) -> HelmOciService:
        if self.helm_factory is not None:
            return self.helm_factory(session)
        return HelmOciService(session, self.settings)

    def _package_service(self) -> BundlePackageService:
        if self.package_factory is not None:
            return self.package_factory()
        return BundlePackageService(self.settings)

    def _require_source_contour(self) -> None:
        if self.settings.portal_contour is not PortalContour.SOURCE:
            raise ExportOrchestrationError(
                "export_wrong_contour",
                "Export доступен только в контуре SOURCE",
            )

    @staticmethod
    def _operation_spec(item: ResolvedExportArtifact) -> OperationArtifactSpec:
        name: str | None = None
        reference: str | None = item.reference
        version: str | None = None
        if item.kind is ArtifactKind.HELM_CHART:
            name = item.repository.rsplit("/", 1)[-1]
            version = item.reference
            reference = None
        return OperationArtifactSpec(
            artifact_type=item.kind.value,
            repository=item.full_repository,
            name=name,
            reference=reference,
            version=version,
            source_digest=item.digest,
            size_bytes=item.size_bytes,
        )

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

    def _prepare_helm_root(self, operation_id: int) -> Path:
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
    def _cleanup_helm_root(path: Path) -> None:
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _fail_artifacts(
        context: OperationContext,
        artifact_ids: Sequence[int],
        code: str,
        message: str,
    ) -> None:
        for artifact_id in artifact_ids:
            context.set_artifact_status(
                artifact_id,
                ArtifactStatus.FAILED,
                error_code=code,
                error_message=message,
            )

    @staticmethod
    def _normalize_harbor_error(exc: HarborClientError) -> ExportOrchestrationError:
        mapping = {
            "unauthorized": ("harbor_auth_failed", "Harbor отклонил учётные данные"),
            "forbidden": ("harbor_forbidden", "Harbor запретил доступ порталу"),
            "timeout": ("harbor_unavailable", "Harbor не ответил вовремя"),
            "tls_failed": ("harbor_tls_failed", "Не удалось проверить TLS Harbor"),
            "connection_failed": (
                "harbor_unavailable",
                "Не удалось подключиться к Harbor",
            ),
            "harbor_unavailable": ("harbor_unavailable", "Harbor временно недоступен"),
            "rate_limited": ("harbor_rate_limited", "Harbor ограничил частоту запросов"),
            "invalid_response": (
                "harbor_invalid_response",
                "Harbor вернул некорректный ответ",
            ),
        }
        code, message = mapping.get(
            exc.code,
            ("harbor_error", "Ошибка обращения к локальному Harbor"),
        )
        return ExportOrchestrationError(code, message)
