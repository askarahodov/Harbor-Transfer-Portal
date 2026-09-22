from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Operation
from app.domain.bundle import OperationStatus, OperationType

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    OperationStatus.COMPLETED,
    OperationStatus.FAILED,
    OperationStatus.REJECTED,
    OperationStatus.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class StorageCleanupReport:
    export_deliveries_removed: int = 0
    import_staging_removed: int = 0
    extraction_workspaces_removed: int = 0
    bytes_reclaimed: int = 0

    @property
    def removed_items(self) -> int:
        return (
            self.export_deliveries_removed
            + self.import_staging_removed
            + self.extraction_workspaces_removed
        )


def cleanup_transfer_storage(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> StorageCleanupReport:
    current = _aware_utc(now or datetime.now(UTC))
    export_cutoff = current - timedelta(seconds=settings.export_bundle_retention_seconds)
    import_cutoff = current - timedelta(seconds=settings.import_bundle_retention_seconds)

    with session_factory() as session:
        operations = list(session.scalars(select(Operation)))

    reclaimed = 0
    export_removed = 0
    import_removed = 0
    extraction_removed = 0

    outgoing_root = settings.bundle_outgoing_root.resolve()
    for operation in operations:
        if (
            operation.type is not OperationType.EXPORT
            or operation.status is not OperationStatus.COMPLETED
            or operation.delivery_id is None
            or _operation_timestamp(operation) > export_cutoff
        ):
            continue
        removed_bytes, removed_any = _remove_export_delivery(
            outgoing_root,
            operation.delivery_id,
        )
        reclaimed += removed_bytes
        export_removed += int(removed_any)

    import_by_storage: dict[str, list[Operation]] = {}
    for operation in operations:
        if operation.type is not OperationType.IMPORT or not operation.import_storage_key:
            continue
        import_by_storage.setdefault(operation.import_storage_key, []).append(operation)

    staging_root = settings.import_staging_root.resolve()
    for storage_key, owners in import_by_storage.items():
        if not _valid_storage_key(storage_key):
            continue
        if any(owner.status not in _TERMINAL_STATUSES for owner in owners):
            continue
        if max(_operation_timestamp(owner) for owner in owners) > import_cutoff:
            continue
        removed_bytes, removed = _remove_direct_child_tree(
            staging_root,
            staging_root / storage_key,
        )
        reclaimed += removed_bytes
        import_removed += int(removed)

    active_import_ids = {
        operation.id
        for operation in operations
        if operation.type is OperationType.IMPORT
        and operation.status not in _TERMINAL_STATUSES
    }
    extract_root = settings.bundle_extract_root.resolve()
    if extract_root.exists() and extract_root.is_dir():
        for candidate in extract_root.glob("import-*"):
            operation_id = _extraction_operation_id(candidate.name)
            if operation_id is None or operation_id in active_import_ids:
                continue
            removed_bytes, removed = _remove_direct_child_tree(extract_root, candidate)
            reclaimed += removed_bytes
            extraction_removed += int(removed)

    report = StorageCleanupReport(
        export_deliveries_removed=export_removed,
        import_staging_removed=import_removed,
        extraction_workspaces_removed=extraction_removed,
        bytes_reclaimed=reclaimed,
    )
    if report.removed_items:
        logger.info(
            "transfer storage cleanup reclaimed persisted payloads",
            extra={
                "export_deliveries_removed": report.export_deliveries_removed,
                "import_staging_removed": report.import_staging_removed,
                "extraction_workspaces_removed": report.extraction_workspaces_removed,
                "bytes_reclaimed": report.bytes_reclaimed,
            },
        )
    return report


async def run_periodic_storage_cleanup(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    while True:
        await asyncio.sleep(settings.storage_cleanup_interval_seconds)
        try:
            await asyncio.to_thread(cleanup_transfer_storage, session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic transfer storage cleanup failed")


def _operation_timestamp(operation: Operation) -> datetime:
    value = operation.finished_at or operation.updated_at or operation.created_at
    return _aware_utc(value)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _valid_storage_key(value: str) -> bool:
    return len(value) == 48 and all(character in "0123456789abcdef" for character in value)


def _extraction_operation_id(name: str) -> int | None:
    prefix = "import-"
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix) :]
    if not suffix.isdigit():
        return None
    value = int(suffix)
    return value if value > 0 else None


def _remove_export_delivery(root: Path, delivery_id: str) -> tuple[int, bool]:
    if (
        not delivery_id.startswith("DELIVERY-")
        or "/" in delivery_id
        or "\\" in delivery_id
        or delivery_id in {".", ".."}
    ):
        return 0, False

    names = (
        f"{delivery_id}.htp.tar.gz",
        f"{delivery_id}.htp.tar.gz.sha256",
        f"{delivery_id}.htp-handoff.json",
    )
    reclaimed = 0
    removed = False
    for name in names:
        path = root / name
        if path.parent.resolve() != root:
            continue
        if path.is_symlink():
            path.unlink(missing_ok=True)
            removed = True
            continue
        if path.is_file():
            reclaimed += path.stat().st_size
            path.unlink(missing_ok=True)
            removed = True
    return reclaimed, removed


def _remove_direct_child_tree(root: Path, child: Path) -> tuple[int, bool]:
    root_resolved = root.resolve()
    if child.parent.resolve() != root_resolved:
        return 0, False
    if child.is_symlink():
        child.unlink(missing_ok=True)
        return 0, True
    if not child.exists():
        return 0, False
    try:
        child.resolve().relative_to(root_resolved)
    except ValueError:
        return 0, False
    if not child.is_dir():
        return 0, False
    size = _tree_size(child)
    shutil.rmtree(child)
    return size, True


def _tree_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total
