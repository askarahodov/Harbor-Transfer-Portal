from __future__ import annotations

import errno
import os
import secrets
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Operation
from app.services.bundle_package_service import (
    BundlePackageError,
    BundlePackageService,
)
from app.services.export_orchestrator import (
    ExportOrchestrator,
    PackageFactory,
    RegistryClientFactory,
)
from app.services.operation_manager import OperationManager


class _OwnedBundlePackageService(BundlePackageService):
    """Publish export bundles without replacing an existing delivery."""

    def __init__(
        self,
        settings: Settings,
        *,
        on_publication_recorded: Callable[[str, str, str, int], None],
        on_publication_rolled_back: Callable[[str], None],
    ) -> None:
        super().__init__(settings)
        self._on_publication_recorded = on_publication_recorded
        self._on_publication_rolled_back = on_publication_rolled_back
        self._published_delivery_id: str | None = None

    def _stage_archive(self, source: Path, stage: Path) -> None:
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

    def _atomic_publish(
        self,
        temporary_archive: Path,
        final_archive: Path,
        final_sidecar: Path,
        archive_sha: str,
    ) -> None:
        """Publish an archive/sidecar pair without replacing another delivery."""
        self.outgoing_root.mkdir(parents=True, exist_ok=True)
        sidecar_tmp = self.outgoing_root / (
            f".{final_sidecar.name}.{secrets.token_hex(6)}.tmp"
        )
        owned_archive = False
        try:
            try:
                self._stage_archive(temporary_archive, final_archive)
            except FileExistsError as exc:
                raise BundlePackageError(
                    "bundle_delivery_exists",
                    "Bundle с таким delivery_id уже опубликован",
                ) from exc
            owned_archive = True
            delivery_id = final_archive.name.removesuffix(".htp.tar.gz")
            self._published_delivery_id = delivery_id
            self._on_publication_recorded(
                delivery_id, final_archive.name, archive_sha, final_archive.stat().st_size
            )
            sidecar_tmp.write_text(
                f"{archive_sha}  {final_archive.name}\n", encoding="utf-8"
            )
            os.chmod(sidecar_tmp, 0o600)
            try:
                os.link(sidecar_tmp, final_sidecar)
            except FileExistsError as exc:
                raise BundlePackageError(
                    "bundle_delivery_exists",
                    "Bundle с таким delivery_id уже содержит sidecar",
                ) from exc
        except Exception:
            if owned_archive:
                final_archive.unlink(missing_ok=True)
                if self._published_delivery_id is not None:
                    self._on_publication_rolled_back(self._published_delivery_id)
                    self._published_delivery_id = None
            raise
        finally:
            sidecar_tmp.unlink(missing_ok=True)

    def _atomic_publish_metadata(self, destination: Path, payload: bytes) -> None:
        temporary = self.outgoing_root / (
            f".{destination.name}.{secrets.token_hex(6)}.tmp"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise BundlePackageError(
                    "bundle_delivery_exists",
                    "Metadata для delivery уже существует",
                ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _rollback_completed_publication(self, delivery_id: str) -> None:
        if self._published_delivery_id != delivery_id:
            return
        archive = self.outgoing_root / f"{delivery_id}.htp.tar.gz"
        sidecar = self.outgoing_root / f"{delivery_id}.htp.tar.gz.sha256"
        handoff = self.outgoing_root / f"{delivery_id}.htp-handoff.json"
        handoff.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        self._on_publication_rolled_back(delivery_id)
        self._published_delivery_id = None

class PublicationSafeExportOrchestrator(ExportOrchestrator):
    """Export orchestrator with explicit ownership of outgoing publication files."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        operation_manager: OperationManager,
        *,
        registry_client_factory: RegistryClientFactory | None = None,
        package_factory: PackageFactory | None = None,
    ) -> None:
        super().__init__(
            session_factory,
            settings,
            operation_manager,
            registry_client_factory=registry_client_factory,
            package_factory=package_factory,
        )
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
        archive_sha: str,
        archive_size: int,
    ) -> None:
        self._publication_lock.acquire()
        try:
            if delivery_id in self._owned_deliveries:
                raise BundlePackageError(
                    "bundle_delivery_exists",
                    "Bundle с таким delivery_id уже опубликован",
                )
            self._owned_deliveries.add(delivery_id)
        finally:
            self._publication_lock.release()

    def _cleanup_published_delivery(self, delivery_id: str) -> None:
        with self._publication_lock:
            if delivery_id not in self._owned_deliveries:
                return
            super()._cleanup_published_delivery(delivery_id)
            self._owned_deliveries.discard(delivery_id)

        with self.session_factory() as session:
            operations = session.scalars(
                select(Operation).where(Operation.delivery_id == delivery_id)
            ).all()
            for operation in operations:
                operation.bundle_filename = None
                operation.bundle_sha256 = None
                operation.bundle_size_bytes = None
                operation.handoff_filename = None
                operation.handoff_sha256 = None
                operation.handoff_size_bytes = None
                operation.bundle_signing_key_fingerprint = None
            session.commit()

    def _rollback_owned_publication(self, delivery_id: str) -> None:
        with self._publication_lock:
            self._owned_deliveries.discard(delivery_id)
