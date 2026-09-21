from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import CurrentUserDep, SessionDep, require_roles
from app.db.models import User, UserRole
from app.schemas.runtime import (
    RuntimeModeResponse,
    RuntimeModeUpdateRequest,
    RuntimeModeUpdateResponse,
)
from app.services.operation_manager import OperationManager, OperationManagerError
from app.services.runtime_mode import RuntimeModeError, RuntimeModeService

router = APIRouter(prefix="/runtime", tags=["runtime"])
RuntimeModeActorDep = Annotated[
    User,
    Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
]


def _runtime_error(exc: RuntimeModeError) -> HTTPException:
    mapping = {
        "runtime_mode_busy": status.HTTP_409_CONFLICT,
        "runtime_mode_switch_in_progress": status.HTTP_409_CONFLICT,
        "runtime_mode_switch_invalid": status.HTTP_409_CONFLICT,
        "runtime_mode_switch_stale": status.HTTP_409_CONFLICT,
        "runtime_mode_cancel_failed": status.HTTP_409_CONFLICT,
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
async def switch_runtime_mode(
    payload: RuntimeModeUpdateRequest,
    actor: RuntimeModeActorDep,
    request: Request,
    session: SessionDep,
) -> RuntimeModeUpdateResponse:
    service = RuntimeModeService(session, request.app.state.settings)
    try:
        preparation = service.begin_switch(payload.mode)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc

    completed = False
    cancelled_operation_ids: list[int] = []
    try:
        if preparation.token is not None:
            manager = cast(OperationManager, request.app.state.operation_manager)
            for operation_id in preparation.blocking_operation_ids:
                try:
                    await manager.cancel(operation_id)
                except OperationManagerError as exc:
                    raise RuntimeModeError(
                        "runtime_mode_cancel_failed",
                        f"Не удалось отменить операцию #{operation_id}: {exc.message}",
                    ) from exc

                operation = manager.get_operation(operation_id)
                if operation is not None and operation.status.value == "CANCELLED":
                    cancelled_operation_ids.append(operation_id)

        result = service.complete_switch(
            preparation,
            actor=actor,
            cancelled_operation_ids=cancelled_operation_ids,
        )
        completed = True
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    finally:
        if not completed:
            service.abort_switch(preparation.token)

    return RuntimeModeUpdateResponse(
        previous=result.previous,
        current=result.current,
        changed=result.changed,
        cancelled_operation_ids=list(result.cancelled_operation_ids),
    )
