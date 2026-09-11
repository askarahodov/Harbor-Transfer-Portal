from __future__ import annotations

import errno
import os
import secrets
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import Operation
from app.domain.bundle import OperationStatus, OperationType
from app.services.bundle_package_service import (
    BundleBuildResult,
    BundlePackageError,
    BundlePackageService,
)
from app.services.export_orchestrator import ExportOrchestrator


PublicationRecordedCallback = Callable[[str, str, str, int], None]
PublicationRolledBackCallback = Callable[[str], None]


class _OwnedBundlePackageService(BundlePackageService):
    """Publish export bundles without replacing an existing delivery."""

    def __init__(
        self,
        settings,
        *,
        on_publication_recorded: PublicationRecordedCallback,
        on_publication_rolled_back: PublicationRolledBackCallback,
    ) -> None:
        super().__init__(settings)
        self._on_publication_recorded = on_publication_recorded
        self._on_publication_rolled_back = on_publication_rolled_back
        self._published_delivery_id: str | None = None

    def build_bundle(self, *args: Any, **kwargs: Any) -> BundleBuildResult:
        self._published_delivery_id = None
        try:
            return super().build_bundle(*args, **kwargs)
        except BaseException:
            if self._published_delivery_id is not None:
                self._rollback_completed_publication(self._published_delivery_id)
            raise

    def _atomic_publish(
        self,
        temporary_archive: Path,
        final_archive: Path,
        final_sidecar: Path,
        archive_sha: str,
    ) -> None:
        self.outgoing_root.mkdir(parents=True, exist_ok=True)
        archive_stage = self.outgoing_root / (
            f".{final_archive.name}.{secrets.token_hex(6)}.archive.tmp"
        )
        sidecar_stage = self.outgoing_root / (
            f".{final_sidecar.name}.{secrets.token_hex(6)}.tmp"
        )
        delivery_id = final_archive.name.removesuffix(".htp.tar.gz")
        published_archive = False
        published_sidecar = False
        ownership_recorded = False
        try:
            self._stage_archive(temporary_archive, archive_stage)
            try:
                os.link(archive_stage, final_archive)
            except FileExistsError as exc:
                raise BundlePackageError(
                    "bundle_delivery_exists",
                    "Bundle с таким delivery_id уже опубликован",
                ) from exc
            published_archive = True
            archive_size = final_archive.stat().st_size

            self._on_publication_recorded(
                delivery_id,
                final_archive.name,
                archive_sha,
                archive_size,
            )
            ownership_recorded = True

            with sidecar_stage.open("x", encoding="utf-8") as stream:
                stream.write(f"{archive_sha}  {final_archive.name}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(sidecar_stage, 0o600)
            try:
                os.link(sidecar_stage, final_sidecar)
            except FileExistsError as exc:
                raise BundlePackageError(
                    "bundle_delivery_exists",
                    "Bundle с таким delivery_id уже опубликован",
                ) from exc
            published_sidecar = True
            temporary_archive.unlink(missing_ok=True)
            self._published_delivery_id = delivery_id
        except Exception:
            if published_sidecar:
                final_sidecar.unlink(missing_ok=True)
            if published_archive:
                final_archive.unlink(missing_ok=True)
            if ownership_recorded:
                self._on_publication_rolled_back(delivery_id)
            raise
        finally:
            sidecar_stage.unlink(missing_ok=True)
            archive_stage.unlink(missing_ok=True)

    @staticmethod
    def _stage_archive(source: Path, stage: Path) -> None:
        try:
            os.link(source, stage)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            with source.open("rb") as input_stream, stage.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        os.chmod(stage, 0o600)

    def _rollback_completed_publication(self, delivery_id: str) -> None:
        archive = self.outgoing_root / f"{delivery_id}.htp.tar.gz"
        sidecar = self.outgoing_root / f"{delivery_id}.htp.tar.gz.sha256"
        sidecar.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        self._on_publication_rolled_back(delivery_id)
        self._published_delivery_id = None


class PublicationSafeExportOrchestrator(ExportOrchestrator):
    """Export orchestrator with explicit ownership of outgoing publication files."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._publication_lock = threading.Lock()
        self._owned_deliveries: set[str] = set()

    def _package_service(self) -> BundlePackageService:
        if self.package_factory is not None:
            return self.package_factory()
        return _OwnedBundlePackageService(
            self.settings,
            on_publication_recorded=self._record_owned_publication,
            on_publication_rolled_back=self._rollback_owned_publication,
        )

    def _record_owned_publication(
        self,
        delivery_id: str,
        archive_name: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        with self.session_factory() as session:
            operation = session.scalar(
                select(Operation).where(
                    Operation.delivery_id == delivery_id,
                    Operation.type == OperationType.EXPORT,
                )
            )
            if operation is None:
                raise BundlePackageError(
                    "export_operation_not_found",
                    "Export-операция исчезла до фиксации ownership публикации",
                )
            if operation.status is OperationStatus.COMPLETED:
                raise BundlePackageError(
                    "export_operation_state_invalid",
                    "Нельзя публиковать bundle для уже завершённой export-операции",
                )
            operation.bundle_filename = archive_name
            operation.bundle_sha256 = sha256
            operation.bundle_size_bytes = size_bytes
            session.commit()

        with self._publication_lock:
            self._owned_deliveries.add(delivery_id)

    def _rollback_owned_publication(self, delivery_id: str) -> None:
        self._clear_owned_metadata(delivery_id)
        with self._publication_lock:
            self._owned_deliveries.discard(delivery_id)

    def _cleanup_published_delivery(self, delivery_id: str) -> None:
        with self._publication_lock:
            owned = delivery_id in self._owned_deliveries
        if not owned:
            return

        super()._cleanup_published_delivery(delivery_id)
        self._clear_owned_metadata(delivery_id)
        with self._publication_lock:
            self._owned_deliveries.discard(delivery_id)

    def _clear_owned_metadata(self, delivery_id: str) -> None:
        with self.session_factory() as session:
            operation = session.scalar(
                select(Operation).where(
                    Operation.delivery_id == delivery_id,
                    Operation.type == OperationType.EXPORT,
                )
            )
            if operation is None:
                return
            operation.bundle_filename = None
            operation.bundle_sha256 = None
            operation.bundle_size_bytes = None
            session.commit()
