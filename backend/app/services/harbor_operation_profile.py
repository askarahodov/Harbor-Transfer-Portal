from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Operation
from app.services.harbor_profiles import (
    DEFAULT_PROFILE_ID,
    HarborProfileService,
    HarborProfileSnapshot,
)
from app.services.harbor_settings import HarborSettingsError


@dataclass(frozen=True, slots=True)
class OperationHarborBinding:
    id: str
    name: str
    url: str


def new_operation_binding(
    session: Session,
    settings: Settings,
    profile_id: str | None,
) -> OperationHarborBinding:
    snapshot: HarborProfileSnapshot = HarborProfileService(session, settings).snapshot(profile_id)
    return OperationHarborBinding(snapshot.id, snapshot.name, snapshot.url)


def operation_binding(
    session: Session,
    settings: Settings,
    operation: Operation,
) -> OperationHarborBinding:
    profile_id = operation.harbor_profile_id or DEFAULT_PROFILE_ID
    snapshot = HarborProfileService(session, settings).snapshot(profile_id)

    # Legacy operations predate the immutable profile snapshot. They remain bound to
    # Default Harbor by compatibility, but there is no historical identity to compare.
    if operation.harbor_profile_id is None:
        return OperationHarborBinding(snapshot.id, snapshot.name, snapshot.url)

    if operation.harbor_url != snapshot.url or operation.harbor_profile_name != snapshot.name:
        raise HarborSettingsError(
            "harbor_profile_snapshot_mismatch",
            "Harbor profile изменился после привязки к operation",
        )
    return OperationHarborBinding(snapshot.id, snapshot.name, snapshot.url)
