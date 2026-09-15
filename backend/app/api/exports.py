from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from app.auth.dependencies import CredentialsDep, SessionDep, require_roles
from app.auth.security import (
    create_export_download_token,
    decode_access_token,
    decode_export_download_token,
)
from app.config import BrowserScheme
from app.db.models import User, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.schemas.exports import (
    ExportBundleResponse,
    ExportDownloadTicketResponse,
    ExportPreviewResponse,
    ExportResolvedArtifactResponse,
    ExportSelectionRequest,
    ExportStartResponse,
)
from app.services.export_orchestrator import ExportOrchestrationError, ExportOrchestrator
from app.services.export_publication_guard import PublicationSafeExportOrchestrator

router = APIRouter(prefix="/exports", tags=["exports"])
ExportActorDep = Annotated[
    User,
    Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
]

_EXPORT_DOWNLOAD_COOKIE = "htp_export_download"
_EXPORT_DOWNLOAD_TTL_SECONDS = 120


def get_export_orchestrator(request: Request) -> ExportOrchestrator:
    return PublicationSafeExportOrchestrator(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.operation_manager,
    )


ExportOrchestratorDep = Annotated[ExportOrchestrator, Depends(get_export_orchestrator)]


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _export_error(exc: ExportOrchestrationError) -> HTTPException:
    mapping = {
        "export_wrong_contour": status.HTTP_409_CONFLICT,
        "export_source_changed": status.HTTP_409_CONFLICT,
        "export_artifact_kind_changed": status.HTTP_409_CONFLICT,
        "export_not_ready": status.HTTP_409_CONFLICT,
        "export_source_not_found": status.HTTP_404_NOT_FOUND,
        "export_operation_not_found": status.HTTP_404_NOT_FOUND,
        "export_artifact_unsupported": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "operation_insufficient_disk": status.HTTP_507_INSUFFICIENT_STORAGE,
        "harbor_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
        "harbor_ca_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "harbor_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "harbor_rate_limited": status.HTTP_503_SERVICE_UNAVAILABLE,
        "harbor_tls_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
        "harbor_auth_failed": status.HTTP_502_BAD_GATEWAY,
        "harbor_forbidden": status.HTTP_502_BAD_GATEWAY,
        "harbor_invalid_response": status.HTTP_502_BAD_GATEWAY,
        "harbor_error": status.HTTP_502_BAD_GATEWAY,
        "export_bundle_missing": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "export_bundle_metadata_invalid": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "export_bundle_path_invalid": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "export_operation_create_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return _api_error(
        mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        exc.code,
        exc.message,
    )


def _authorize_operation(
    orchestrator: ExportOrchestrator,
    operation_id: int,
    actor: User,
) -> None:
    operation = orchestrator.operation_manager.get_operation(operation_id)
    if operation is None or operation.type is not OperationType.EXPORT:
        raise _api_error(
            status.HTTP_404_NOT_FOUND,
            "export_operation_not_found",
            "Export-операция не найдена",
        )
    if actor.role is not UserRole.ADMIN and operation.actor_user_id != actor.id:
        raise _api_error(
            status.HTTP_403_FORBIDDEN,
            "export_forbidden",
            "Operator может получать только собственный export bundle",
        )


def _jwt_secret(request: Request) -> str:
    secret = request.app.state.settings.jwt_secret
    if secret is None:
        raise _api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_not_configured",
            "Authentication не настроена",
        )
    return str(secret.get_secret_value())


def _active_user(session: SessionDep, user_id: int) -> User:
    user = UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise _api_error(
            status.HTTP_401_UNAUTHORIZED,
            "download_auth_invalid",
            "Download authorization недействительна или истекла",
        )
    if user.role not in {UserRole.ADMIN, UserRole.OPERATOR}:
        raise _api_error(
            status.HTTP_403_FORBIDDEN,
            "export_forbidden",
            "Недостаточно прав для скачивания export bundle",
        )
    return user


def _authorize_download_request(
    *,
    request: Request,
    operation_id: int,
    credentials: CredentialsDep,
    session: SessionDep,
    orchestrator: ExportOrchestrator,
) -> None:
    secret = _jwt_secret(request)
    if credentials is not None:
        try:
            user_id = decode_access_token(credentials.credentials, secret)
        except ValueError as exc:
            raise _api_error(
                status.HTTP_401_UNAUTHORIZED,
                "download_auth_invalid",
                "Download authorization недействительна или истекла",
            ) from exc
        actor = _active_user(session, user_id)
        _authorize_operation(orchestrator, operation_id, actor)
        return

    ticket = request.cookies.get(_EXPORT_DOWNLOAD_COOKIE)
    if not ticket:
        raise _api_error(
            status.HTTP_401_UNAUTHORIZED,
            "download_auth_required",
            "Для скачивания требуется короткоживущий download ticket",
        )
    try:
        user_id = decode_export_download_token(
            ticket,
            secret,
            expected_operation_id=operation_id,
        )
    except ValueError as exc:
        raise _api_error(
            status.HTTP_401_UNAUTHORIZED,
            "download_auth_invalid",
            "Download ticket недействителен или истёк",
        ) from exc
    actor = _active_user(session, user_id)
    _authorize_operation(orchestrator, operation_id, actor)


@router.post("/preview", response_model=ExportPreviewResponse)
def preview_export(
    payload: ExportSelectionRequest,
    _actor: ExportActorDep,
    orchestrator: ExportOrchestratorDep,
) -> ExportPreviewResponse:
    try:
        resolved = orchestrator.preview(payload.artifacts)
    except ExportOrchestrationError as exc:
        raise _export_error(exc) from exc
    return ExportPreviewResponse(
        artifacts=[
            ExportResolvedArtifactResponse(
                kind=item.kind,
                project=item.project,
                repository=item.repository,
                reference=item.reference,
                source_digest=item.digest,
                size_bytes=item.size_bytes,
            )
            for item in resolved
        ],
        estimated_payload_bytes=sum(item.size_bytes or 0 for item in resolved),
    )


@router.post("", response_model=ExportStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_export(
    payload: ExportSelectionRequest,
    actor: ExportActorDep,
    orchestrator: ExportOrchestratorDep,
) -> ExportStartResponse:
    try:
        result = await orchestrator.start_export(
            payload.artifacts,
            actor_user_id=actor.id,
            actor_username=actor.username,
            comment=payload.comment,
        )
    except ExportOrchestrationError as exc:
        raise _export_error(exc) from exc
    return ExportStartResponse(
        operation_id=result.operation_id,
        delivery_id=result.delivery_id,
        status=OperationStatus.CREATED,
    )


@router.get("/{operation_id}/bundle", response_model=ExportBundleResponse)
def export_bundle_metadata(
    operation_id: int,
    actor: ExportActorDep,
    orchestrator: ExportOrchestratorDep,
) -> ExportBundleResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        metadata = orchestrator.bundle_metadata(operation_id)
    except ExportOrchestrationError as exc:
        raise _export_error(exc) from exc
    return ExportBundleResponse(
        operation_id=metadata.operation_id,
        delivery_id=metadata.delivery_id,
        archive_name=metadata.archive_path.name,
        archive_size=metadata.archive_size,
        sha256=metadata.sha256,
        download_url=f"/api/exports/{operation_id}/download",
    )


@router.post(
    "/{operation_id}/download-ticket",
    response_model=ExportDownloadTicketResponse,
)
def create_download_ticket(
    operation_id: int,
    request: Request,
    response: Response,
    actor: ExportActorDep,
    orchestrator: ExportOrchestratorDep,
) -> ExportDownloadTicketResponse:
    _authorize_operation(orchestrator, operation_id, actor)
    try:
        orchestrator.bundle_metadata(operation_id)
    except ExportOrchestrationError as exc:
        raise _export_error(exc) from exc

    token = create_export_download_token(
        user_id=actor.id,
        operation_id=operation_id,
        secret=_jwt_secret(request),
        lifetime_seconds=_EXPORT_DOWNLOAD_TTL_SECONDS,
    )
    download_path = f"/api/exports/{operation_id}/download"
    response.set_cookie(
        key=_EXPORT_DOWNLOAD_COOKIE,
        value=token,
        max_age=_EXPORT_DOWNLOAD_TTL_SECONDS,
        httponly=True,
        secure=(
            request.app.state.settings.portal_browser_scheme is BrowserScheme.HTTPS
        ),
        samesite="strict",
        path=download_path,
    )
    return ExportDownloadTicketResponse(
        download_url=download_path,
        expires_in_seconds=_EXPORT_DOWNLOAD_TTL_SECONDS,
    )


@router.get("/{operation_id}/download", response_class=FileResponse)
def download_export_bundle(
    operation_id: int,
    request: Request,
    credentials: CredentialsDep,
    session: SessionDep,
    orchestrator: ExportOrchestratorDep,
) -> FileResponse:
    _authorize_download_request(
        request=request,
        operation_id=operation_id,
        credentials=credentials,
        session=session,
        orchestrator=orchestrator,
    )
    try:
        metadata = orchestrator.bundle_metadata(operation_id)
    except ExportOrchestrationError as exc:
        raise _export_error(exc) from exc
    return FileResponse(
        path=metadata.archive_path,
        filename=metadata.archive_path.name,
        media_type="application/gzip",
        headers={
            "X-Checksum-SHA256": metadata.sha256,
            "Cache-Control": "no-store",
        },
    )
