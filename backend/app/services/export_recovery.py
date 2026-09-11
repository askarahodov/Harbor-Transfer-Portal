from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Operation
from app.domain.bundle import OperationStatus, OperationType


def reconcile_incomplete_export_publications(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> int:
    """Remove ready-looking files that belong to non-completed export operations."""
    root = settings.bundle_outgoing_root.resolve()
    cleaned = 0
    with session_factory() as session:
        operations = list(
            session.scalars(
                select(Operation).where(
                    Operation.type == OperationType.EXPORT,
                    Operation.status != OperationStatus.COMPLETED,
                )
            )
        )
        for operation in operations:
            changed = False
            if operation.delivery_id:
                archive = root / f"{operation.delivery_id}.htp.tar.gz"
                sidecar = root / f"{operation.delivery_id}.htp.tar.gz.sha256"
                for path in (sidecar, archive):
                    if path.parent.resolve() != root:
                        continue
                    if path.exists() or path.is_symlink():
                        path.unlink(missing_ok=True)
                        changed = True
            if (
                operation.bundle_filename is not None
                or operation.bundle_sha256 is not None
                or operation.bundle_size_bytes is not None
            ):
                operation.bundle_filename = None
                operation.bundle_sha256 = None
                operation.bundle_size_bytes = None
                changed = True
            if changed:
                cleaned += 1
        session.commit()
    return cleaned
