from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import require_roles
from app.db.models import User, UserRole
from app.domain.bundle import OperationStatus, OperationType
from app.schemas.imports import (
    ImportDiscoveryResponse,
    ImportExecuteRequest,
    ImportIntakeResponse,
    ImportPreviewResponse,
    ImportReceiptResponse,
    ImportStartResponse,
)
from app.services.import_helm_service import ImportHelmOciService
from app.services.import_orchestrator import ImportOrchestrationError, ImportOrchestrator
from app.services.import_preview_projection import ImportPreviewProjectionOrchestrator

router = APIRouter(prefix="/imports", tags=["imports"])
ImportActorDep = Annotated[
    User,
    Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
]


def get_import_orchestrator(request: Request) -> ImportOrchestrator:
    settings = request.app.state.settings
    return ImportPreviewProjectionOrchestrator(
        request.app.state.session_factory,
        settings,
        request.app.state.operation_manager,
        helm_factory=lambda session: ImportHelmOciService(session, settings),
    )


ImportOrchestratorDep = Annotated[ImportOrchestrator, Depends(get_import_orchestrator)]


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _import_error(exc: ImportOrchestrationError) -> HTTPException:
    mapping = {
        "import_wrong_contour": status.HTTP_409_CONFLICT,
        "import_upload_empty": status.HTTP_400_BAD_REQUEST,
        "import_upload_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
        "import_operation_not_found": status.HTTP_404_NOT_FOUND,
        "import_preview_not_ready": status.HTTP_409_CONFLICT,
        "import_receipt_not_ready": status.HTTP_409_CONFLICT,
        "import_not_ready": status.HTTP_409_CONFLICT,
        "import_preview_unresolved": status.HTTP_409_CONFLICT,
        "import_conflict_blocked": status.HTTP_409_CONFLICT,
        "import_overwrite_disabled": status.HTTP_403_FORBIDDEN,
        "operation_worker_already_running": status.HTTP_409_CONFLICT,
        "operation_insufficient_disk": status.HTTP_507_INSUFFICIENT_STORAGE,
        "import_operation_create_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return _api_error(
        mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        exc.code,
        exc.message,
    )


def _authorize_operation(
    orchestrator: ImportOrchestrator,
    operation_id: int,
    actor: User,
) -> None:
    operation = orchestrator.operation_manager.get_operation(operation_id)
    if operation is None or operation.type is not OperationType.IMPORT:
        raise _api_error(
            status.HTTP_404_NOT_FOUND,
            "import_operation_not_found",
            "Import-операция не найдена",
        )
    if actor.role is not UserRole.ADMIN and operation.actor_user_id != actor.id:
        raise _api_error(
            status.HTTP_403_FORBIDDEN,
            "import_forbidden",
            "Operator может управлять только собственными import operations",
        )


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise _api_error(
            status.HTTP_400_BAD_REQUEST,
            "import_content_length_invalid",
            "Content-Length должен быть целым числом",
        ) from exc
    if value < 0:
        raise _api_error(
            status.HTTP_400_BAD_REQUEST,
            "import_content_length_invalid",
            "Content-Length не может быть отрицательным",
        )
    return value


@router.post(
    "/upload",
    response_model=ImportIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_bundle(
    request: Request,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> ImportIntakeResponse:
    try:
        started = await orchestrator.accept_upload(
            request.stream(),
            content_length=_content_length(request),
            actor_user_id=actor.id,
            actor_username=actor.username,
        )
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc
    return ImportIntakeResponse(
        operation_id=started.operation_id,
        status=started.status,
        intake_mode=started.intake_mode,
    )


@router.post(
    "/discover",
    response_model=ImportDiscoveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def discover_incoming_bundles(
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> ImportDiscoveryResponse:
    try:
        discovered = await orchestrator.discover_ready(
            actor_user_id=actor.id,
            actor_username=actor.username,
        )
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc
    return ImportDiscoveryResponse(
        operations=[
            ImportIntakeResponse(
                operation_id=item.operation_id,
                status=item.status,
                intake_mode=item.intake_mode,
            )
            for item in discovered
        ]
    )


@router.get("/{operation_id}/preview", response_model=ImportPreviewResponse)
def import_preview(
    operation_id: int,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> ImportPreviewResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        return orchestrator.preview(operation_id)
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc


@router.post(
    "/{operation_id}/execute",
    response_model=ImportStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_import(
    operation_id: int,
    payload: ImportExecuteRequest,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> ImportStartResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        await orchestrator.start_import(
            operation_id,
            actor_username=actor.username,
            overwrite_conflicts=payload.overwrite_conflicts,
        )
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc
    return ImportStartResponse(
        operation_id=operation_id,
        status=OperationStatus.IMPORTING,
    )


@router.get("/{operation_id}/receipt", response_model=ImportReceiptResponse)
def import_receipt(
    operation_id: int,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> ImportReceiptResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        return orchestrator.receipt(operation_id)
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc
