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
    """Remove only publications whose ownership is persisted on a non-completed export."""
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
            owned = _has_persisted_publication_ownership(operation)
            handoff_owned = _has_persisted_handoff_ownership(operation)
            if operation.delivery_id and (owned or handoff_owned):
                paths = (
                    [root / f"{operation.delivery_id}.htp.tar.gz.sha256",
                     root / f"{operation.delivery_id}.htp.tar.gz"]
                    if owned else []
                )
                # A partially committed signed handoff is also a publication.
                # Only remove it when the operation owns the exact delivery ID.
                paths.append(root / f"{operation.delivery_id}.htp-handoff.json")
                for path in paths:
                    if path.parent.resolve() != root:
                        continue
                    if path.exists() or path.is_symlink():
                        path.unlink(missing_ok=True)
                        changed = True

            if owned or handoff_owned or _has_any_bundle_metadata(operation):
                operation.bundle_filename = None
                operation.bundle_sha256 = None
                operation.bundle_size_bytes = None
                operation.handoff_filename = None
                operation.handoff_sha256 = None
                operation.handoff_size_bytes = None
                operation.bundle_signing_key_fingerprint = None
                changed = True

            if changed:
                cleaned += 1
        session.commit()
    return cleaned


def _has_persisted_publication_ownership(operation: Operation) -> bool:
    if operation.delivery_id is None:
        return False
    expected_name = f"{operation.delivery_id}.htp.tar.gz"
    digest = operation.bundle_sha256
    size = operation.bundle_size_bytes
    return (
        operation.bundle_filename == expected_name
        and digest is not None
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        and size is not None
        and size >= 0
    )


def _has_persisted_handoff_ownership(operation: Operation) -> bool:
    if operation.delivery_id is None:
        return False
    digest = operation.handoff_sha256
    size = operation.handoff_size_bytes
    return (
        operation.handoff_filename == f"{operation.delivery_id}.htp-handoff.json"
        and digest is not None
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        and size is not None
        and size >= 0
    )


def _has_any_bundle_metadata(operation: Operation) -> bool:
    return (
        operation.bundle_filename is not None
        or operation.bundle_sha256 is not None
        or operation.bundle_size_bytes is not None
        or operation.handoff_filename is not None
        or operation.handoff_sha256 is not None
        or operation.handoff_size_bytes is not None
        or operation.bundle_signing_key_fingerprint is not None
    )
