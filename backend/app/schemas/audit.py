from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEventResponse(BaseModel):
    id: int
    actor_user_id: int | None
    actor_username: str
    event_type: str
    result: str
    metadata: dict[str, Any]
    created_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int
