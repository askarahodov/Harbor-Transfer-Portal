from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Operation, User
from app.db.repositories import AuditEventRepository


def operation_audit_metadata(
    operation: Operation,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "operation_id": operation.id,
        "operation_type": operation.type.value,
        "delivery_id": operation.delivery_id,
        "source_delivery_id": operation.source_delivery_id,
    }
    if extra:
        metadata.update(extra)
    return metadata


def audit_actor_operation(
    session: Session,
    *,
    actor: User,
    event_type: str,
    operation: Operation,
    result: str = "success",
    extra: dict[str, Any] | None = None,
) -> None:
    AuditEventRepository(session).create(
        actor=actor,
        event_type=event_type,
        result=result,
        metadata=operation_audit_metadata(operation, extra=extra),
    )
