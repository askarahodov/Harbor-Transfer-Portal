import json
from datetime import UTC, datetime

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection

from app.db.models import AuditEvent, Operation
from app.domain.bundle import OperationStatus, OperationType

_registered = False


def _event_type(operation: Operation, previous: OperationStatus, current: OperationStatus) -> str | None:
    prefix = "export" if operation.type is OperationType.EXPORT else "import"
    if operation.type is OperationType.IMPORT and previous is OperationStatus.VERIFYING:
        if current is OperationStatus.READY:
            return "import.verified"
        if current is OperationStatus.REJECTED:
            return "import.rejected"
    if current is OperationStatus.COMPLETED:
        return f"{prefix}.completed"
    if current is OperationStatus.FAILED:
        return f"{prefix}.failed"
    if current is OperationStatus.CANCELLED:
        return f"{prefix}.cancelled"
    return None


def _result(current: OperationStatus) -> str:
    if current in {OperationStatus.FAILED, OperationStatus.REJECTED}:
        return "failure"
    if current is OperationStatus.CANCELLED:
        return "cancelled"
    return "success"


def _audit_operation_update(_mapper: object, connection: Connection, operation: Operation) -> None:
    history = inspect(operation).attrs.status.history
    if not history.has_changes() or not history.deleted or not history.added:
        return
    previous = history.deleted[0]
    current = history.added[0]
    event_type = _event_type(operation, previous, current)
    if event_type is None:
        return

    metadata = {
        "operation_id": operation.id,
        "delivery_id": operation.delivery_id,
        "source_delivery_id": operation.source_delivery_id,
        "operation_type": operation.type.value,
        "status": current.value,
        "error_code": operation.error_code,
        "total_artifacts": operation.total_artifacts,
        "successful_artifacts": operation.successful_artifacts,
        "failed_artifacts": operation.failed_artifacts,
        "skipped_artifacts": operation.skipped_artifacts,
        "conflict_artifacts": operation.conflict_artifacts,
    }
    now = datetime.now(UTC)
    connection.execute(
        AuditEvent.__table__.insert().values(
            actor_user_id=None,
            actor_username="system",
            event_type=event_type,
            result=_result(current),
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            created_at=now,
            updated_at=now,
        )
    )


def register_operation_audit_hooks() -> None:
    global _registered
    if _registered:
        return
    event.listen(Operation, "after_update", _audit_operation_update)
    _registered = True
