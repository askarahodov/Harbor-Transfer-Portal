from __future__ import annotations

import errno
import os
import secrets
import shutil
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Operation
from app.domain.bundle import BundleSource, OperationStatus, OperationType
from app.services.bundle_package_service import (
    BundleBuildResult,
    BundlePackageError,
    BundlePackageService,
    PackageArtifactInput,
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

    def build_bundle(
        self,
        artifacts: list[PackageArtifactInput],
        operation_id: int,
    ) -> BundleBuildResult:
        result = super().build_bundle(artifacts, operation_id)
        if result.delivery_id is not None:
            self._published_delivery_id = result.delivery_id
            self._on_publication_recorded(
                result.delivery_id,
                result.archive_path.name,
                result.sha256,
                result.archive_size,
            )
        return result

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

    def _rollback_completed_publication(self, delivery_id: str) -> None:
        archive = self.outgoing_root / f"{delivery_id}.htp.tar.gz"
        sidecar = self.outgoing_root / f"{delivery_id}.htp.tar.gz.sha256"
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

    def _rollback_owned_publication(self, delivery_id: str) -> None:
        self._publication_lock.acquire()
        try:
            self._owned_deliveries.discard(delivery_id)
            super()._rollback_completed_publication(delivery_id)
        finally:
            self._publication_lock.release()