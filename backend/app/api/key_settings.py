from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.auth.dependencies import SessionDep, require_roles
from app.config import PortalContour
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.keys import (
    KeyMaterialRequest,
    KeyMutationResponse,
    KeySettingsResponse,
    SigningKeyStatusResponse,
    SigningRotationRequest,
    TrustedKeyMaterialRequest,
    TrustedKeyStateRequest,
    TrustedKeyStatusResponse,
)
from app.services.key_management import KeyManagementError, KeyManagementService, KeyMutation
from app.services.runtime_mode import RuntimeModeError, RuntimeModeService, RuntimeModeSnapshot
from app.services.source_trust_package import SourceTrustPackageService, TrustPackageError

router = APIRouter(prefix="/settings/keys", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _service(request: Request) -> KeyManagementService:
    return KeyManagementService(request.app.state.settings)


def _api_error(exc: KeyManagementError) -> HTTPException:
    mapping = {
        "key_management_wrong_contour": status.HTTP_409_CONFLICT,
        "trusted_key_not_found": status.HTTP_404_NOT_FOUND,
        "trusted_key_limit_exceeded": status.HTTP_409_CONFLICT,
        "trusted_key_confirmation_required": status.HTTP_409_CONFLICT,
        "signing_key_already_configured": status.HTTP_409_CONFLICT,
        "signing_key_not_configured": status.HTTP_409_CONFLICT,
        "pending_signing_key_already_configured": status.HTTP_409_CONFLICT,
        "pending_signing_key_not_configured": status.HTTP_409_CONFLICT,
        "pending_signing_key_fingerprint_mismatch": status.HTTP_409_CONFLICT,
        "key_store_write_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return HTTPException(
        status_code=mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        detail={"code": exc.code, "message": exc.message},
    )


def _trust_package_error(exc: TrustPackageError) -> HTTPException:
    mapping = {
        "trust_package_wrong_contour": status.HTTP_409_CONFLICT,
        "trusted_key_limit_exceeded": status.HTTP_409_CONFLICT,
        "trust_package_size_invalid": status.HTTP_413_CONTENT_TOO_LARGE,
        "trust_package_too_large": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return HTTPException(
        status_code=mapping.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        detail={"code": exc.code, "message": exc.message},
    )


async def _bounded_request_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise _trust_package_error(
                TrustPackageError(
                    "trust_package_size_invalid",
                    "Content-Length trust package некорректен",
                )
            ) from exc
        if declared < 1 or declared > max_bytes:
            raise _trust_package_error(
                TrustPackageError(
                    "trust_package_size_invalid",
                    "Trust package пуст или превышает допустимый размер",
                )
            )

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise _trust_package_error(
                TrustPackageError(
                    "trust_package_size_invalid",
                    "Trust package превышает допустимый размер",
                )
            )
    if not payload:
        raise _trust_package_error(
            TrustPackageError("trust_package_size_invalid", "Trust package пуст")
        )
    return bytes(payload)


def _runtime_error(exc: RuntimeModeError) -> HTTPException:
    if exc.code == "runtime_mode_mismatch":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "key_management_wrong_contour",
                "message": exc.message,
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": exc.code, "message": exc.message},
    )


def _require_confirmation(confirm: bool) -> None:
    if not confirm:
        raise _api_error(
            KeyManagementError(
                "trusted_key_confirmation_required",
                "Изменение TARGET trust set требует явного подтверждения",
            )
        )


def _audit(
    session: SessionDep,
    admin: User,
    event_type: str,
    mutation: KeyMutation,
    runtime: RuntimeModeSnapshot,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    metadata: dict[str, object] = {
        "action": mutation.action,
        "fingerprint": mutation.fingerprint,
        "runtime_mode": runtime.mode.value,
        "runtime_mode_version": runtime.version,
    }
    if extra:
        metadata.update(extra)
    AuditEventRepository(session).create(
        actor=admin,
        event_type=event_type,
        metadata=metadata,
    )
    session.commit()


def _runtime_service(request: Request, session: SessionDep) -> RuntimeModeService:
    return RuntimeModeService(session, request.app.state.settings)


@router.get("", response_model=KeySettingsResponse)
def get_key_settings(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> KeySettingsResponse:
    try:
        with _runtime_service(request, session).mode_guard() as runtime:
            service = _service(request)
            if runtime.mode is PortalContour.SOURCE:
                signing = service.signing_status()
                pending = service.pending_signing_status()
                return KeySettingsResponse(
                    contour=runtime.mode,
                    signing_key=SigningKeyStatusResponse(
                        configured=signing.configured,
                        fingerprint=signing.fingerprint,
                    ),
                    pending_signing_key=SigningKeyStatusResponse(
                        configured=pending.configured,
                        fingerprint=pending.fingerprint,
                    ),
                )
            trusted = service.list_trusted_keys()
            return KeySettingsResponse(
                contour=runtime.mode,
                trusted_keys=[
                    TrustedKeyStatusResponse(
                        fingerprint=item.fingerprint,
                        enabled=item.enabled,
                    )
                    for item in trusted
                ],
            )
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc


@router.post(
    "/signing/generate",
    response_model=KeyMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_signing_key(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            mutation = _service(request).generate_signing_private_key()
            _audit(session, admin, "signing.key.generated", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.post(
    "/signing/rotation/prepare",
    response_model=KeyMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def prepare_signing_rotation(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            mutation = _service(request).prepare_pending_signing_key()
            _audit(session, admin, "signing.rotation.prepared", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.post(
    "/signing/rotation/activate",
    response_model=KeyMutationResponse,
)
def activate_signing_rotation(
    payload: SigningRotationRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            mutation = _service(request).activate_pending_signing_key(
                payload.expected_fingerprint
            )
            _audit(session, admin, "signing.rotation.activated", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.post(
    "/signing/rotation/cancel",
    response_model=KeyMutationResponse,
)
def cancel_signing_rotation(
    payload: SigningRotationRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            mutation = _service(request).cancel_pending_signing_key(
                payload.expected_fingerprint
            )
            _audit(session, admin, "signing.rotation.cancelled", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.get("/signing/rotation/trust-package")
def download_pending_trust_package(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> Response:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            package = SourceTrustPackageService(
                request.app.state.settings
            ).build_pending()
            _audit(
                session,
                admin,
                "signing.rotation.trust_package.exported",
                KeyMutation(action="exported", fingerprint=package.fingerprint),
                runtime,
            )
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except TrustPackageError as exc:
        raise _trust_package_error(exc) from exc

    return Response(
        content=package.payload,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{package.filename}"',
            "Cache-Control": "no-store",
            "X-Signing-Key-Fingerprint": package.fingerprint,
        },
    )


@router.get("/signing/public")
def download_signing_public_key(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> Response:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE):
            public_key = _service(request).signing_public_key()
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc

    return Response(
        content=public_key.pem,
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": 'attachment; filename="source-signing-public.pem"',
            "Cache-Control": "no-store",
            "X-Signing-Key-Fingerprint": public_key.fingerprint,
        },
    )


@router.get("/signing/trust-package")
def download_source_trust_package(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> Response:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            package = SourceTrustPackageService(request.app.state.settings).build()
            _audit(
                session,
                admin,
                "signing.trust_package.exported",
                KeyMutation(action="exported", fingerprint=package.fingerprint),
                runtime,
            )
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except TrustPackageError as exc:
        raise _trust_package_error(exc) from exc

    return Response(
        content=package.payload,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{package.filename}"',
            "Cache-Control": "no-store",
            "X-Signing-Key-Fingerprint": package.fingerprint,
        },
    )


@router.post(
    "/trusted/package",
    response_model=KeyMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_source_trust_package(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = Query(False),
) -> KeyMutationResponse:
    _require_confirmation(confirm)
    payload = await _bounded_request_body(
        request,
        request.app.state.settings.bundle_trust_package_max_bytes,
    )
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.TARGET) as runtime:
            mutation = SourceTrustPackageService(
                request.app.state.settings
            ).import_package(payload)
            _audit(
                session,
                admin,
                "trust.source_identity.imported",
                mutation,
                runtime,
                extra={"package_format": "htp-trust-v1"},
            )
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except TrustPackageError as exc:
        raise _trust_package_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.put("/signing", response_model=KeyMutationResponse)
def install_signing_key(
    payload: KeyMaterialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.SOURCE) as runtime:
            mutation = _service(request).install_signing_private_key(payload.pem)
            _audit(session, admin, f"signing.key.{mutation.action}", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.post(
    "/trusted",
    response_model=KeyMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_trusted_key(
    payload: TrustedKeyMaterialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    _require_confirmation(payload.confirm)
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.TARGET) as runtime:
            mutation = _service(request).add_trusted_public_key(payload.pem)
            _audit(session, admin, f"trust.key.{mutation.action}", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.put("/trusted/{fingerprint}", response_model=KeyMutationResponse)
def replace_trusted_key(
    fingerprint: str,
    payload: TrustedKeyMaterialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    _require_confirmation(payload.confirm)
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.TARGET) as runtime:
            mutation = _service(request).replace_trusted_public_key(fingerprint, payload.pem)
            _audit(
                session,
                admin,
                "trust.key.replaced",
                mutation,
                runtime,
                extra={"previous_fingerprint": fingerprint.strip().lower()},
            )
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.patch("/trusted/{fingerprint}", response_model=KeyMutationResponse)
def set_trusted_key_state(
    fingerprint: str,
    payload: TrustedKeyStateRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> KeyMutationResponse:
    _require_confirmation(payload.confirm)
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.TARGET) as runtime:
            mutation = _service(request).set_trusted_key_enabled(fingerprint, payload.enabled)
            _audit(session, admin, f"trust.key.{mutation.action}", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)


@router.delete("/trusted/{fingerprint}", response_model=KeyMutationResponse)
def remove_trusted_key(
    fingerprint: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    confirm: bool = Query(default=False),
) -> KeyMutationResponse:
    _require_confirmation(confirm)
    try:
        with _runtime_service(request, session).mode_guard(PortalContour.TARGET) as runtime:
            mutation = _service(request).remove_trusted_key(fingerprint)
            _audit(session, admin, "trust.key.removed", mutation, runtime)
    except RuntimeModeError as exc:
        raise _runtime_error(exc) from exc
    except KeyManagementError as exc:
        raise _api_error(exc) from exc
    return KeyMutationResponse(action=mutation.action, fingerprint=mutation.fingerprint)
