from fastapi import HTTPException

from app.db.models import Operation, User, UserRole
from app.domain.bundle import OperationType


def authorize_operation(
    operation: Operation | None,
    operation_type: OperationType,
    actor: User,
    *,
    not_found: HTTPException,
    forbidden: HTTPException,
) -> None:
    if operation is None or operation.type is not operation_type:
        raise not_found
    if actor.role is not UserRole.ADMIN and operation.actor_user_id != actor.id:
        raise forbidden
