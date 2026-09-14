from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import SessionDep, require_roles
from app.config import PortalContour
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.keys import (
    KeyConfirmationRequest,
    KeyManagementStatusResponse,
    SourceSigningKeyInstallRequest,
    SourceSigningKeyStatusResponse,
    TrustedPublicKeyInstallRequest,
    TrustedPublicKeyResponse,
)
from app.services.key_management import KeyManagementError, KeyManagementService

router = APIRouter(prefix="/settings/keys", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _service(request: Request) -> KeyManagementService:
    return KeyManagementService(request.app.state.settings)


def _api_error(exc: KeyManagementError) -> HTTPException:
    if exc.code == "key_trusted_not_found":
        code = status.HTTP_404_NOT_FOUND
    elif exc.code in {"key_wrong_contour", "key_trust_limit_exceeded"}:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=code, detail={"code": exc.code, "message": exc.message})


def _trusted_response(fingerprint: str, enabled: bool) -> TrustedPublicKeyResponse:
    return TrustedPublicKeyResponse(fingerprint=fingerprint, enabled=enabled)


def _audit(
    session: SessionDep,
    admin: User,
    event_type: str,
    metadata: dict[str, object],
) -> None:
    AuditEventRepository(session).create(
        actor=admin,
        event_type=event_type,
        metadata=metadata,
    )
    session.commit()


@router.get("", response_model=KeyManagementStatusResponse)
def get_key_status(
    request: Request,
    _admin: AdminDep,
) -> KeyManagementStatusResponse:
    service = _service(request)
    try:
        if service.settings.portal_contour is PortalContour.SOURCE:
            source = service.source_status()
            return KeyManagementStatusResponse(
                contour=PortalContour.SOURCE,
                source_signing=SourceSigningKeyStatusResponse(
                    configured=source.configured,
                    fingerprint=source.fingerprint,
                ),
            )
        trusted = service.list_trusted_keys()
        return KeyManagementStatusResponse(
            contour=PortalContour.TARGET,
            trusted_keys=[
                _trusted_response(item.fingerprint, item.enabled) for item in trusted
            ],
        )
    except KeyManagementError as exc:
        raise _api_error(exc) from exc


@router.put("/source-signing", response_model=SourceSigningKeyStatusResponse)
def install_source_signing_key(
    payload: SourceSigningKeyInstallRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> SourceSigningKeyStatusResponse:
    service = _service(request)
    try:
        previous = service.source_status()
        result = service.install_source_private_key(payload.private_key_pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "keys.source_signing.rotated" if previous.configured else "keys.source_signing.installed",
        {"fingerprint": result.fingerprint},
    )
    return SourceSigningKeyStatusResponse(
        configured=result.configured,
        fingerprint=result.fingerprint,
    )


@router.post("/trusted", response_model=TrustedPublicKeyResponse)
def add_trusted_public_key(
    payload: TrustedPublicKeyInstallRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TrustedPublicKeyResponse:
    service = _service(request)
    try:
        result = service.add_trusted_key(payload.public_key_pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "keys.trusted.added",
        {"fingerprint": result.fingerprint},
    )
    return _trusted_response(result.fingerprint, result.enabled)


@router.post("/trusted/{fingerprint}/enable", response_model=TrustedPublicKeyResponse)
def enable_trusted_public_key(
    fingerprint: str,
    _payload: KeyConfirmationRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TrustedPublicKeyResponse:
    service = _service(request)
    try:
        result = service.set_trusted_key_enabled(fingerprint, enabled=True)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, "keys.trusted.enabled", {"fingerprint": result.fingerprint})
    return _trusted_response(result.fingerprint, result.enabled)


@router.post("/trusted/{fingerprint}/disable", response_model=TrustedPublicKeyResponse)
def disable_trusted_public_key(
    fingerprint: str,
    _payload: KeyConfirmationRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TrustedPublicKeyResponse:
    service = _service(request)
    try:
        result = service.set_trusted_key_enabled(fingerprint, enabled=False)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, "keys.trusted.disabled", {"fingerprint": result.fingerprint})
    return _trusted_response(result.fingerprint, result.enabled)


@router.post("/trusted/{fingerprint}/replace", response_model=TrustedPublicKeyResponse)
def replace_trusted_public_key(
    fingerprint: str,
    payload: TrustedPublicKeyInstallRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TrustedPublicKeyResponse:
    service = _service(request)
    try:
        result = service.replace_trusted_key(fingerprint, payload.public_key_pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "keys.trusted.replaced",
        {"old_fingerprint": fingerprint, "new_fingerprint": result.fingerprint},
    )
    return _trusted_response(result.fingerprint, result.enabled)


@router.post("/trusted/{fingerprint}/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove_trusted_public_key(
    fingerprint: str,
    _payload: KeyConfirmationRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> None:
    service = _service(request)
    try:
        service.remove_trusted_key(fingerprint)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, "keys.trusted.removed", {"fingerprint": fingerprint})
