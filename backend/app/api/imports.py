import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.auth.dependencies import SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.domain.bundle import OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportDestinationPlanRequest,
    ImportDestinationPlanResponse,
    ImportDiscoveryResponse,
    ImportExecuteRequest,
    ImportIntakeResponse,
    ImportPreviewResponse,
    ImportReceiptResponse,
    ImportStartResponse,
    MediaHandoffVerificationResponse,
)
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator
from app.services.import_helm_service import ImportHelmOciService
from app.services.import_mapping_audit import destination_plan_audit_metadata
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.media_handoff import MediaHandoffError, MediaHandoffService
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator
from app.services.report_service import receipt_filename

router = APIRouter(prefix="/imports", tags=["imports"])
ImportActorDep = Annotated[
    User,
    Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
]


def get_import_orchestrator(request: Request) -> PolicyAwareImportDestinationPlanOrchestrator:
    settings = request.app.state.settings
    return PolicyAwareImportDestinationPlanOrchestrator(
        request.app.state.session_factory,
        settings,
        request.app.state.operation_manager,
        helm_factory=lambda session: ImportHelmOciService(session, settings),
    )


ImportOrchestratorDep = Annotated[
    PolicyAwareImportDestinationPlanOrchestrator,
    Depends(get_import_orchestrator),
]


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
        "import_conflict_policy_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "import_overwrite_disabled": status.HTTP_403_FORBIDDEN,
        "import_signing_key_untrusted": status.HTTP_409_CONFLICT,
        "trusted_key_store_invalid": status.HTTP_503_SERVICE_UNAVAILABLE,
        "import_destination_plan_not_ready": status.HTTP_409_CONFLICT,
        "import_destination_plan_required": status.HTTP_409_CONFLICT,
        "import_destination_plan_stale": status.HTTP_409_CONFLICT,
        "import_destination_plan_invalid": status.HTTP_409_CONFLICT,
        "import_destination_plan_actor_missing": status.HTTP_409_CONFLICT,
        "import_destination_plan_actor_mismatch": status.HTTP_403_FORBIDDEN,
        "import_destination_override_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "harbor_not_configured": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "harbor_configuration_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "harbor_profile_binding_invalid": status.HTTP_409_CONFLICT,
        "harbor_profile_changed": status.HTTP_409_CONFLICT,
        "harbor_profile_selection_locked": status.HTTP_409_CONFLICT,
        "harbor_profile_disabled": status.HTTP_409_CONFLICT,
        "harbor_profile_not_found": status.HTTP_404_NOT_FOUND,
        "operation_worker_already_running": status.HTTP_409_CONFLICT,
        "operation_insufficient_disk": status.HTTP_507_INSUFFICIENT_STORAGE,
        "import_operation_create_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "handoff_signer_untrusted": status.HTTP_409_CONFLICT,
        "handoff_file_missing": status.HTTP_409_CONFLICT,
        "handoff_file_mismatch": status.HTTP_409_CONFLICT,
        "handoff_delivery_mismatch": status.HTTP_409_CONFLICT,
        "handoff_signature_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "handoff_not_canonical": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "handoff_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "handoff_file_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
    }
    return _api_error(
        mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        exc.code,
        exc.message,
    )


def _handoff_error(exc: MediaHandoffError) -> HTTPException:
    mapping = {
        "handoff_wrong_contour": status.HTTP_409_CONFLICT,
        "handoff_size_invalid": status.HTTP_413_CONTENT_TOO_LARGE,
        "handoff_signer_untrusted": status.HTTP_409_CONFLICT,
        "handoff_file_missing": status.HTTP_409_CONFLICT,
        "handoff_file_mismatch": status.HTTP_409_CONFLICT,
        "handoff_signature_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "handoff_not_canonical": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "handoff_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "handoff_file_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
    }
    return _api_error(
        mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        exc.code,
        exc.message,
    )


async def _read_handoff_body(request: Request) -> bytes:
    limit = request.app.state.settings.bundle_max_metadata_bytes
    declared = _content_length(request)
    if declared is not None and declared > limit:
        raise _api_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "handoff_size_invalid",
            "Handoff manifest превышает metadata limit",
        )
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > limit:
            raise _api_error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "handoff_size_invalid",
                "Handoff manifest превышает metadata limit",
            )
    if not payload:
        raise _api_error(
            status.HTTP_400_BAD_REQUEST,
            "handoff_empty",
            "Handoff manifest пуст",
        )
    return bytes(payload)


def _authorize_operation(
    orchestrator: ImportDestinationPlanOrchestrator,
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


def _decode_browser_metadata_header(request: Request, name: str) -> bytes | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    if len(raw) > 16_384:
        raise _api_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "handoff_size_invalid",
            "Browser handoff metadata превышают header limit",
        )
    try:
        payload = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _api_error(
            status.HTTP_400_BAD_REQUEST,
            "handoff_invalid",
            "Browser handoff metadata имеют неверный base64 encoding",
        ) from exc
    if not payload:
        raise _api_error(
            status.HTTP_400_BAD_REQUEST,
            "handoff_invalid",
            "Browser handoff metadata пусты",
        )
    return payload


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


def _audit_import_start(
    session: SessionDep,
    actor: User,
    orchestrator: ImportDestinationPlanOrchestrator,
    operation_id: int,
    *,
    overwrite_conflicts: bool,
    skip_conflicts: bool,
    destination_plan: ImportDestinationPlanResponse,
) -> None:
    operation = orchestrator.operation_manager.get_operation(operation_id)
    conflict_count = sum(
        item.classification is ImportPreviewState.CONFLICT
        for item in destination_plan.artifacts
    )
    metadata: dict[str, object] = {
        "operation_id": operation_id,
        "bundle_sha256": destination_plan.bundle_sha256,
        "overwrite_conflicts": overwrite_conflicts,
        "skip_conflicts": skip_conflicts,
        "conflict_count": conflict_count,
        **destination_plan_audit_metadata(destination_plan),
    }
    if operation is not None and operation.source_delivery_id:
        metadata["source_delivery_id"] = operation.source_delivery_id

    repository = AuditEventRepository(session)
    repository.create(
        actor=actor,
        event_type="import.started",
        result="started",
        metadata=metadata,
    )
    if overwrite_conflicts and conflict_count > 0:
        repository.create(
            actor=actor,
            event_type="import.overwrite.approved",
            result="approved",
            metadata={
                key: value
                for key, value in metadata.items()
                if key != "overwrite_conflicts"
            },
        )
    if skip_conflicts and conflict_count > 0:
        repository.create(
            actor=actor,
            event_type="import.conflicts.skipped",
            result="approved",
            metadata={
                key: value
                for key, value in metadata.items()
                if key != "skip_conflicts"
            },
        )
    session.commit()


@router.post(
    "/handoff/verify",
    response_model=MediaHandoffVerificationResponse,
)
async def verify_physical_handoff(
    request: Request,
    actor: ImportActorDep,
    session: SessionDep,
) -> MediaHandoffVerificationResponse:
    payload = await _read_handoff_body(request)
    try:
        result = MediaHandoffService(
            request.app.state.settings
        ).verify_from_discovery(payload)
    except MediaHandoffError as exc:
        raise _handoff_error(exc) from exc

    AuditEventRepository(session).create(
        actor=actor,
        event_type="physical.handoff.verified",
        result="verified",
        metadata={
            "delivery_id": result.delivery_id,
            "signing_key_fingerprint": result.signing_key_fingerprint,
            "bundle_sha256": result.bundle_sha256,
            "bundle_size_bytes": result.bundle_size_bytes,
        },
    )
    session.commit()
    return MediaHandoffVerificationResponse(
        delivery_id=result.delivery_id,
        signing_key_fingerprint=result.signing_key_fingerprint,
        bundle_sha256=result.bundle_sha256,
        bundle_size_bytes=result.bundle_size_bytes,
        created_at=result.created_at,
        created_by=result.created_by,
    )


@router.post(
    "/upload",
    response_model=ImportIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_bundle(
    request: Request,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
    profile_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> ImportIntakeResponse:
    try:
        started = await orchestrator.accept_upload(
            request.stream(),
            content_length=_content_length(request),
            actor_user_id=actor.id,
            actor_username=actor.username,
            bundle_filename=request.headers.get("x-htp-bundle-filename"),
            sidecar_payload=_decode_browser_metadata_header(
                request,
                "x-htp-sidecar-base64",
            ),
            handoff_payload=_decode_browser_metadata_header(
                request,
                "x-htp-handoff-base64",
            ),
            harbor_profile_id=profile_id,
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
    profile_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> ImportDiscoveryResponse:
    try:
        discovered = await orchestrator.discover_ready(
            actor_user_id=actor.id,
            actor_username=actor.username,
            harbor_profile_id=profile_id,
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


@router.put(
    "/{operation_id}/destination-plan",
    response_model=ImportDestinationPlanResponse,
)
async def import_destination_plan(
    operation_id: int,
    payload: ImportDestinationPlanRequest,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> ImportDestinationPlanResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        return await orchestrator.build_destination_plan(
            operation_id,
            payload,
            actor_username=actor.username,
        )
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
    session: SessionDep,
) -> ImportStartResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        await orchestrator.start_import(
            operation_id,
            actor_username=actor.username,
            overwrite_conflicts=payload.overwrite_conflicts,
            skip_conflicts=payload.skip_conflicts,
            destination_plan_id=payload.destination_plan_id,
        )
        destination_plan = orchestrator.destination_plan(operation_id)
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc
    _audit_import_start(
        session,
        actor,
        orchestrator,
        operation_id,
        overwrite_conflicts=payload.overwrite_conflicts,
        skip_conflicts=payload.skip_conflicts,
        destination_plan=destination_plan,
    )
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


@router.get("/{operation_id}/receipt/download")
def download_import_receipt(
    operation_id: int,
    actor: ImportActorDep,
    orchestrator: ImportOrchestratorDep,
) -> Response:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        receipt = orchestrator.receipt(operation_id)
    except ImportOrchestrationError as exc:
        raise _import_error(exc) from exc
    payload = receipt.model_dump_json(indent=2) + "\n"
    filename = receipt_filename(operation_id)
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
