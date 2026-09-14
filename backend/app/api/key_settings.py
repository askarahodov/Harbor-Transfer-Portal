from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth.dependencies import SessionDep, require_roles
from app.config import PortalContour
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.key_management import (
    KeyManagementResponse,
    SigningIdentityResponse,
    SigningKeyInstallRequest,
    TrustedKeyInstallRequest,
    TrustedKeyResponse,
    TrustedKeyStateRequest,
)
from app.services.key_management import (
    KeyManagementError,
    KeyManagementService,
    SigningIdentityStatus,
    TrustedKeyStatus,
)

router = APIRouter(prefix="/settings/keys", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _service(request: Request) -> KeyManagementService:
    return KeyManagementService(request.app.state.settings)


def _api_error(exc: KeyManagementError) -> HTTPException:
    if exc.code == "trusted_key_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code in {
        "key_management_wrong_contour",
        "signing_rotation_confirmation_required",
        "trusted_key_confirmation_required",
        "trusted_key_exists",
        "trusted_key_duplicate",
        "trusted_key_limit_exceeded",
    }:
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _signing_response(value: SigningIdentityStatus) -> SigningIdentityResponse:
    return SigningIdentityResponse(
        configured=value.configured,
        key_id=value.key_id,
        fingerprint=value.fingerprint,
    )


def _trusted_response(value: TrustedKeyStatus) -> TrustedKeyResponse:
    return TrustedKeyResponse(
        key_id=value.key_id,
        fingerprint=value.fingerprint,
        enabled=value.enabled,
    )


def _response(service: KeyManagementService) -> KeyManagementResponse:
    try:
        if service.settings.portal_contour is PortalContour.SOURCE:
            return KeyManagementResponse(
                contour=PortalContour.SOURCE,
                signing=_signing_response(service.signing_status()),
                trusted_keys=[],
            )
        return KeyManagementResponse(
            contour=PortalContour.TARGET,
            signing=None,
            trusted_keys=[_trusted_response(item) for item in service.list_trusted_keys()],
        )
    except KeyManagementError as exc:
        raise _api_error(exc) from exc


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


@router.get("", response_model=KeyManagementResponse)
def get_key_management(
    request: Request,
    _admin: AdminDep,
) -> KeyManagementResponse:
    return _response(_service(request))


@router.put("/signing", response_model=KeyManagementResponse)
def install_signing_key(
    payload: SigningKeyInstallRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyManagementResponse:
    service = _service(request)
    try:
        before, after = service.install_signing_private_key(
            payload.private_key_pem.get_secret_value(),
            confirm_rotation=payload.confirm_rotation,
        )
    except KeyManagementError as exc:
        raise _api_error(exc) from exc

    _audit(
        session,
        admin,
        "signing.key.rotated" if before.configured else "signing.key.installed",
        {
            "previous_key_id": before.key_id,
            "previous_fingerprint": before.fingerprint,
            "new_key_id": after.key_id,
            "new_fingerprint": after.fingerprint,
        },
    )
    session.commit()
    return _response(service)


@router.post("/trusted", response_model=KeyManagementResponse)
def add_trusted_key(
    payload: TrustedKeyInstallRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyManagementResponse:
    if not payload.confirm:
        raise _api_error(
            KeyManagementError(
                "trusted_key_confirmation_required",
                "Добавление trusted SOURCE public key требует явного подтверждения",
            )
        )
    service = _service(request)
    try:
        added = service.add_trusted_key(payload.public_key_pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "trust.key.added",
        {"key_id": added.key_id, "fingerprint": added.fingerprint},
    )
    session.commit()
    return _response(service)


@router.put("/trusted/{key_id}", response_model=KeyManagementResponse)
def replace_trusted_key(
    key_id: str,
    payload: TrustedKeyInstallRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyManagementResponse:
    service = _service(request)
    try:
        before, after = service.replace_trusted_key(
            key_id,
            payload.public_key_pem,
            confirm=payload.confirm,
        )
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "trust.key.replaced",
        {
            "previous_key_id": before.key_id,
            "previous_fingerprint": before.fingerprint,
            "new_key_id": after.key_id,
            "new_fingerprint": after.fingerprint,
        },
    )
    session.commit()
    return _response(service)


@router.patch("/trusted/{key_id}", response_model=KeyManagementResponse)
def set_trusted_key_state(
    key_id: str,
    payload: TrustedKeyStateRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyManagementResponse:
    service = _service(request)
    try:
        changed = service.set_trusted_key_enabled(
            key_id,
            enabled=payload.enabled,
            confirm=payload.confirm,
        )
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "trust.key.enabled" if changed.enabled else "trust.key.disabled",
        {
            "key_id": changed.key_id,
            "fingerprint": changed.fingerprint,
        },
    )
    session.commit()
    return _response(service)


@router.delete("/trusted/{key_id}", response_model=KeyManagementResponse)
def remove_trusted_key(
    key_id: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = Query(default=False),
) -> KeyManagementResponse:
    service = _service(request)
    try:
        removed = service.remove_trusted_key(key_id, confirm=confirm)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(
        session,
        admin,
        "trust.key.removed",
        {
            "key_id": removed.key_id,
            "fingerprint": removed.fingerprint,
            "was_enabled": removed.enabled,
        },
    )
    session.commit()
    return _response(service)
