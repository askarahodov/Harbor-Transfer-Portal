from __future__ import annotations

import json
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Operation
from app.domain.bundle import OperationStatus, OperationType

_INSTALLED = False
_PENDING_NEW_OPERATIONS = "htp_audit_new_operations"

_TERMINAL_EVENT_TYPES: dict[tuple[OperationType, OperationStatus], tuple[str, str]] = {
    (OperationType.EXPORT, OperationStatus.COMPLETED): ("export.completed", "success"),
    (OperationType.EXPORT, OperationStatus.FAILED): ("export.failed", "failure"),
    (OperationType.EXPORT, OperationStatus.CANCELLED): ("export.cancelled", "cancelled"),
    (OperationType.IMPORT, OperationStatus.COMPLETED): ("import.completed", "success"),
    (OperationType.IMPORT, OperationStatus.FAILED): ("import.failed", "failure"),
    (OperationType.IMPORT, OperationStatus.REJECTED): ("import.rejected", "failure"),
    (OperationType.IMPORT, OperationStatus.CANCELLED): ("import.cancelled", "cancelled"),
}


def install_operation_audit_hooks() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _audit_operation_changes)
    event.listen(Session, "after_flush_postexec", _audit_new_operations)
    _INSTALLED = True


def _metadata(operation: Operation) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "operation_id": operation.id,
        "operation_type": operation.type.value,
    }
    if operation.delivery_id:
        metadata["delivery_id"] = operation.delivery_id
    if operation.source_delivery_id:
        metadata["source_delivery_id"] = operation.source_delivery_id
    if operation.error_code:
        metadata["error_code"] = operation.error_code
    return metadata


def _append_event(
    session: Session,
    *,
    actor_user_id: int | None,
    actor_username: str,
    event_type: str,
    result: str,
    metadata: dict[str, Any],
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            event_type=event_type,
            result=result,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
    )


def _audit_operation_changes(session: Session, _flush_context: object, _instances: object) -> None:
    new_operations = [candidate for candidate in session.new if isinstance(candidate, Operation)]
    if new_operations:
        pending = session.info.setdefault(_PENDING_NEW_OPERATIONS, [])
        pending.extend(new_operations)

    for candidate in tuple(session.dirty):
        if not isinstance(candidate, Operation):
            continue
        history = inspect(candidate).attrs.status.history
        if not history.has_changes() or not history.added:
            continue
        target = history.added[0]

        if candidate.type is OperationType.IMPORT and target is OperationStatus.VERIFYING:
            _append_event(
                session,
                actor_user_id=candidate.actor_user_id,
                actor_username=candidate.actor_username,
                event_type="import.verification.started",
                result="started",
                metadata=_metadata(candidate),
            )
            continue

        if candidate.type is OperationType.IMPORT and target is OperationStatus.READY:
            _append_event(
                session,
                actor_user_id=None,
                actor_username="system",
                event_type="import.verification.succeeded",
                result="success",
                metadata=_metadata(candidate),
            )
            continue

        terminal = _TERMINAL_EVENT_TYPES.get((candidate.type, target))
        if terminal is None:
            continue
        event_type, result = terminal
        _append_event(
            session,
            actor_user_id=None,
            actor_username="system",
            event_type=event_type,
            result=result,
            metadata=_metadata(candidate),
        )


def _audit_new_operations(session: Session, _flush_context: object) -> None:
    pending = session.info.pop(_PENDING_NEW_OPERATIONS, [])
    for operation in pending:
        if operation.id is None:
            continue
        event_type = (
            "export.created"
            if operation.type is OperationType.EXPORT
            else "import.intake.created"
        )
        _append_event(
            session,
            actor_user_id=operation.actor_user_id,
            actor_username=operation.actor_username,
            event_type=event_type,
            result="started",
            metadata=_metadata(operation),
        )
