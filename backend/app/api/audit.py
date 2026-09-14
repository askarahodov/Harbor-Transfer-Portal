import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import SessionDep, require_roles
from app.db.models import AuditEvent, User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.audit import AuditEventListResponse, AuditEventResponse

router = APIRouter(prefix="/audit/events", tags=["audit"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _metadata(event: AuditEvent) -> dict[str, Any]:
    try:
        value = json.loads(event.metadata_json)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _serialize(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=event.id,
        actor_user_id=event.actor_user_id,
        actor_username=event.actor_username,
        event_type=event.event_type,
        result=event.result,
        metadata=_metadata(event),
        created_at=event.created_at,
    )


@router.get("", response_model=AuditEventListResponse)
def list_audit_events(
    _admin: AdminDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    event_type: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    result: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    actor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
) -> AuditEventListResponse:
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="created_from must be earlier than or equal to created_to",
        )
    items, total = AuditEventRepository(session).list_filtered(
        limit=limit,
        offset=offset,
        event_type=event_type,
        result=result,
        actor_username=actor,
        created_from=created_from,
        created_to=created_to,
    )
    return AuditEventListResponse(
        items=[_serialize(event) for event in items],
        total=total,
        limit=limit,
        offset=offset,
    )
