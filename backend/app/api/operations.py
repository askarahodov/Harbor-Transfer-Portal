from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import CurrentUserDep, SessionDep, require_roles
from app.db.models import Operation, User, UserRole
from app.db.repositories import OperationRepository
from app.schemas.operations import (
    OperationArtifactResponse,
    OperationDetailResponse,
    OperationProgressResponse,
)
from app.services.operation_manager import OperationManager, OperationManagerError

router = APIRouter(prefix="/operations", tags=["operations"])
OperatorDep = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
]


def _manager(request: Request) -> OperationManager:
    return request.app.state.operation_manager


def _to_response(operation: Operation, manager: OperationManager) -> OperationDetailResponse:
    snapshot = manager.get_snapshot(operation.id)
    return OperationDetailResponse(
        id=operation.id,
        delivery_id=operation.delivery_id,
        type=operation.type,
        status=operation.status,
        actor_user_id=operation.actor_user_id,
        actor_username=operation.actor_username,
        comment=operation.comment,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        started_at=operation.started_at,
        finished_at=operation.finished_at,
        error_code=operation.error_code,
        error_message=operation.error_message,
        progress=OperationProgressResponse(
            current=snapshot.progress_current,
            total=snapshot.progress_total,
            completed_artifacts=snapshot.completed_artifacts,
            running_artifacts=snapshot.running_artifacts,
            successful_artifacts=snapshot.successful_artifacts,
            failed_artifacts=snapshot.failed_artifacts,
            skipped_artifacts=snapshot.skipped_artifacts,
            conflict_artifacts=snapshot.conflict_artifacts,
            current_artifact_id=snapshot.current_artifact_id,
        ),
        artifacts=[OperationArtifactResponse.model_validate(item) for item in operation.artifacts],
    )


@router.get("/{operation_id}", response_model=OperationDetailResponse)
def get_operation(
    operation_id: int,
    session: SessionDep,
    _user: CurrentUserDep,
    manager: Annotated[OperationManager, Depends(_manager)],
) -> OperationDetailResponse:
    operation = OperationRepository(session).get(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    return _to_response(operation, manager)


@router.post("/{operation_id}/cancel", response_model=OperationDetailResponse)
async def cancel_operation(
    operation_id: int,
    request: Request,
    session: SessionDep,
    user: OperatorDep,
) -> OperationDetailResponse:
    operation = OperationRepository(session).get(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    if user.role is not UserRole.ADMIN and operation.actor_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator can cancel only own operations",
        )

    manager: OperationManager = request.app.state.operation_manager
    try:
        await manager.cancel(operation_id)
    except OperationManagerError as exc:
        if exc.code == "operation_not_found":
            http_status = status.HTTP_404_NOT_FOUND
        elif exc.code in {"operation_not_cancellable", "operation_not_running"}:
            http_status = status.HTTP_409_CONFLICT
        else:
            http_status = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=http_status, detail=exc.message) from exc

    refreshed = OperationRepository(session).get(operation_id)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    session.refresh(refreshed)
    return _to_response(refreshed, manager)
