from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.imports import _authorize_operation, _import_error, get_import_orchestrator
from app.auth.dependencies import SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.import_retries import ImportRetryRequest, ImportRetryResponse
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.import_retry import ImportRetryService
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator

router = APIRouter(prefix="/imports", tags=["imports"])
RetryActorDep = Annotated[
    User,
    Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
]
RetryOrchestratorDep = Annotated[
    PolicyAwareImportDestinationPlanOrchestrator,
    Depends(get_import_orchestrator),
]


def _retry_error(exc: ImportOrchestrationError) -> HTTPException:
    if exc.code in {
        "import_retry_not_allowed",
        "import_retry_plan_mismatch",
        "import_retry_state_invalid",
    }:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        )
    return _import_error(exc)


@router.post(
    "/{operation_id}/retry",
    response_model=ImportRetryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_import_retry(
    operation_id: int,
    payload: ImportRetryRequest,
    actor: RetryActorDep,
    orchestrator: RetryOrchestratorDep,
    session: SessionDep,
) -> ImportRetryResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        prepared = await ImportRetryService(orchestrator).prepare_retry(
            operation_id,
            actor_user_id=actor.id,
            actor_username=actor.username,
            destination_plan_id=payload.destination_plan_id,
        )
    except ImportOrchestrationError as exc:
        raise _retry_error(exc) from exc

    AuditEventRepository(session).create(
        actor=actor,
        event_type="import.retry.prepared",
        result="prepared",
        metadata={
            "operation_id": prepared.operation_id,
            "retry_of_operation_id": prepared.retry_of_operation_id,
            "bundle_sha256": prepared.destination_plan.bundle_sha256,
            "destination_plan_id": prepared.destination_plan.plan_id,
            "destination_plan_hash": prepared.destination_plan.plan_hash,
            "failure_policy": prepared.failure_policy,
        },
    )
    session.commit()
    return prepared
