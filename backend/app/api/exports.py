from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.auth.dependencies import require_roles
from app.db.models import User, UserRole
from app.schemas.exports import (
    ExportBundleMetadataResponse,
    ExportPreviewResponse,
    ExportSelectionRequest,
    ExportStartResponse,
)
from app.services.export_orchestration import (
    ExportOrchestrationError,
    ExportOrchestrationService,
)

router = APIRouter(prefix="/exports", tags=["exports"])
OperatorDep = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
]


def _service(request: Request) -> ExportOrchestrationService:
    return ExportOrchestrationService(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.operation_manager,
    )


def _raise_api_error(exc: ExportOrchestrationError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    ) from exc


@router.post("/validate", response_model=ExportPreviewResponse)
async def validate_export(
    payload: ExportSelectionRequest,
    request: Request,
    _user: OperatorDep,
) -> ExportPreviewResponse:
    try:
        return await _service(request).preview(payload)
    except ExportOrchestrationError as exc:
        _raise_api_error(exc)


@router.post(
    "",
    response_model=ExportStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_export(
    payload: ExportSelectionRequest,
    request: Request,
    user: OperatorDep,
) -> ExportStartResponse:
    try:
        handle, delivery_id = await _service(request).start(payload, user)
    except ExportOrchestrationError as exc:
        _raise_api_error(exc)
    return ExportStartResponse(
        operation_id=handle.operation_id,
        delivery_id=delivery_id,
        status_url=f"/api/operations/{handle.operation_id}",
        bundle_url=f"/api/exports/{handle.operation_id}/bundle",
    )


@router.get(
    "/{operation_id}/bundle",
    response_model=ExportBundleMetadataResponse,
)
def bundle_metadata(
    operation_id: int,
    request: Request,
    user: OperatorDep,
) -> ExportBundleMetadataResponse:
    try:
        bundle = _service(request).published_bundle(operation_id, user)
    except ExportOrchestrationError as exc:
        _raise_api_error(exc)
    return ExportBundleMetadataResponse(
        operation_id=bundle.operation_id,
        delivery_id=bundle.delivery_id,
        filename=bundle.filename,
        checksum_filename=bundle.checksum_filename,
        size_bytes=bundle.size_bytes,
        sha256=bundle.sha256,
        artifact_count=bundle.artifact_count,
        download_url=f"/api/exports/{operation_id}/bundle/download",
        checksum_url=f"/api/exports/{operation_id}/bundle/checksum",
    )


@router.get("/{operation_id}/bundle/download", response_class=FileResponse)
def download_bundle(
    operation_id: int,
    request: Request,
    user: OperatorDep,
) -> FileResponse:
    try:
        bundle = _service(request).published_bundle(operation_id, user)
    except ExportOrchestrationError as exc:
        _raise_api_error(exc)
    return FileResponse(
        path=bundle.archive_path,
        media_type="application/gzip",
        filename=bundle.filename,
        content_disposition_type="attachment",
    )


@router.get("/{operation_id}/bundle/checksum", response_class=FileResponse)
def download_checksum(
    operation_id: int,
    request: Request,
    user: OperatorDep,
) -> FileResponse:
    try:
        bundle = _service(request).published_bundle(operation_id, user)
    except ExportOrchestrationError as exc:
        _raise_api_error(exc)
    return FileResponse(
        path=bundle.sidecar_path,
        media_type="text/plain; charset=utf-8",
        filename=bundle.checksum_filename,
        content_disposition_type="attachment",
    )
