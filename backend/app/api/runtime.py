from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import CurrentUserDep, SessionDep, require_roles
from app.db.models import User, UserRole
from app.schemas.runtime import (
    RuntimeModeResponse,
    RuntimeModeUpdateRequest,
    RuntimeModeUpdateResponse,
)
from app.services.runtime_mode import RuntimeModeError, RuntimeModeService

router = APIRouter(prefix="/runtime", tags=["runtime"])
RuntimeModeActorDep = Annotated[
    User,
    Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
]


def _runtime_error(exc: RuntimeModeError) -> HTTPException:
    mapping = {
        "runtime_mode_busy": status.HTTP_409_CONFLICT,
        "runtime_mode_invalid": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return HTTPException(
        status_code=mapping.get(exc.code, status.HTTP_409_CONFLICT),
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("", response_model=RuntimeModeResponse)
def get_runtime_mode(
    _user: CurrentUserDep,
    request: Request,
    session: SessionDep,
) -> RuntimeModeResponse:
    try:
        mode = RuntimeModeService(session, request.app.state.settings).current()
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    return RuntimeModeResponse(mode=mode, version=request.app.state.settings.app_version)


@router.put("/mode", response_model=RuntimeModeUpdateResponse)
def switch_runtime_mode(
    payload: RuntimeModeUpdateRequest,
    actor: RuntimeModeActorDep,
    request: Request,
    session: SessionDep,
) -> RuntimeModeUpdateResponse:
    try:
        result = RuntimeModeService(session, request.app.state.settings).switch(
            payload.mode,
            actor=actor,
        )
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    return RuntimeModeUpdateResponse(
        previous=result.previous,
        current=result.current,
        changed=result.changed,
    )
