from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import SessionDep, require_roles
from app.config import PortalContour
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.keys import (
    KeyMaterialRequest,
    KeyMutationResponse,
    KeySettingsResponse,
    SigningKeyStatusResponse,
    TrustedKeyReplaceRequest,
    TrustedKeyStateRequest,
    TrustedKeyStatusResponse,
)
from app.services.key_management import KeyManagementError, KeyManagementService, KeyMutation

router = APIRouter(prefix="/settings/keys", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _service(request: Request) -> KeyManagementService:
    return KeyManagementService(request.app.state.settings)


def _api_error(exc: KeyManagementError) -> HTTPException:
    mapping = {
        "key_management_wrong_contour": status.HTTP_409_CONFLICT,
        "trusted_key_not_found": status.HTTP_404_NOT_FOUND,
        "trusted_key_limit_exceeded": status.HTTP_409_CONFLICT,
        "trusted_key_replace_same": status.HTTP_409_CONFLICT,
        "trusted_key_already_exists": status.HTTP_409_CONFLICT,
        "key_store_write_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return HTTPException(
        status_code=mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        detail={"code": exc.code, "message": exc.message},
    )


def _require_confirmation(confirm: bool) -> None:
    if confirm:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "key_mutation_confirmation_required",
            "message": "TARGET trust mutation требует явного confirm=true",
        },
    )


def _audit(
    session: SessionDep,
    admin: User,
    event_type: str,
    mutation: KeyMutation,
) -> None:
    metadata: dict[str, str] = {
        "action": mutation.action,
        "fingerprint": mutation.fingerprint,
    }
    if mutation.previous_fingerprint is not None:
        metadata["old_fingerprint"] = mutation.previous_fingerprint
        metadata["new_fingerprint"] = mutation.fingerprint
    AuditEventRepository(session).create(
        actor=admin,
        event_type=event_type,
        metadata=metadata,
    )
    session.commit()


@router.get("", response_model=KeySettingsResponse)
def get_key_settings(
    request: Request,
    _admin: AdminDep,
) -> KeySettingsResponse:
    service = _service(request)
    contour = request.app.state.settings.portal_contour
    try:
        if contour is PortalContour.SOURCE:
            signing = service.signing_status()
            return KeySettingsResponse(
                contour=contour,
                signing_key=SigningKeyStatusResponse(
                    configured=signing.configured,
                    fingerprint=signing.fingerprint,
                ),
            )
        trusted = service.list_trusted_keys()
        return KeySettingsResponse(
            contour=contour,
            trusted_keys=[
                TrustedKeyStatusResponse(
                    fingerprint=item.fingerprint,
                    enabled=item.enabled,
                )
                for item in trusted
            ],
        )
    except KeyManagementError as exc:
        raise _api_error(exc) from exc


@router.put("/signing", response_model=KeyMutationResponse)
def install_signing_key(
    payload: KeyMaterialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    service = _service(request)
    try:
        mutation = service.install_signing_private_key(payload.pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, f"signing.key.{mutation.action}", mutation)
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.post(
    "/trusted",
    response_model=KeyMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_trusted_key(
    payload: KeyMaterialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = False,
) -> KeyMutationResponse:
    _require_confirmation(confirm)
    service = _service(request)
    try:
        mutation = service.add_trusted_public_key(payload.pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, f"trust.key.{mutation.action}", mutation)
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.patch("/trusted/{fingerprint}", response_model=KeyMutationResponse)
def set_trusted_key_state(
    fingerprint: str,
    payload: TrustedKeyStateRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = False,
) -> KeyMutationResponse:
    _require_confirmation(confirm)
    service = _service(request)
    try:
        mutation = service.set_trusted_key_enabled(fingerprint, payload.enabled)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, f"trust.key.{mutation.action}", mutation)
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.put("/trusted/{fingerprint}/replace", response_model=KeyMutationResponse)
def replace_trusted_key(
    fingerprint: str,
    payload: TrustedKeyReplaceRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = False,
) -> KeyMutationResponse:
    _require_confirmation(confirm)
    service = _service(request)
    try:
        mutation = service.replace_trusted_public_key(fingerprint, payload.pem)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, "trust.key.replaced", mutation)
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.delete("/trusted/{fingerprint}", response_model=KeyMutationResponse)
def remove_trusted_key(
    fingerprint: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = False,
) -> KeyMutationResponse:
    _require_confirmation(confirm)
    service = _service(request)
    try:
        mutation = service.remove_trusted_key(fingerprint)
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    _audit(session, admin, "trust.key.removed", mutation)
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)
