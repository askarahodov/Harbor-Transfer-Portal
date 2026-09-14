from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.harbor import HarborClientDep
from app.auth.dependencies import SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.settings import (
    HarborCARequest,
    HarborConnectionTestResponse,
    HarborCredentialRequest,
    HarborMutationResponse,
    HarborSettingsPatch,
    HarborSettingsResponse,
    TransferSettingsPatch,
    TransferSettingsResponse,
)
from app.services.harbor_client import HarborClientError
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService
from app.services.transfer_settings import (
    RESTART_REQUIRED_FIELDS,
    TransferSettingsError,
    TransferSettingsService,
)

router = APIRouter(prefix="/settings/harbor", tags=["settings"])
transfer_router = APIRouter(prefix="/settings/transfer", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _service(request: Request, session: SessionDep) -> HarborSettingsService:
    return HarborSettingsService(session, request.app.state.settings)


def _transfer_service(request: Request, session: SessionDep) -> TransferSettingsService:
    return TransferSettingsService(session, request.app.state.settings)


def _response(service: HarborSettingsService) -> HarborSettingsResponse:
    resolved = service.resolve()
    return HarborSettingsResponse(
        contour=service.settings.portal_contour,
        url=resolved.url,
        username=resolved.username,
        verify_tls=resolved.verify_tls,
        credential_configured=service.credential_configured(),
        custom_ca_configured=service.custom_ca_configured(),
    )


def _transfer_response(service: TransferSettingsService) -> TransferSettingsResponse:
    resolved = service.resolve()
    return TransferSettingsResponse(
        import_allow_overwrite=resolved.import_allow_overwrite,
        import_max_upload_bytes=resolved.import_max_upload_bytes,
        bundle_max_archive_bytes=resolved.bundle_max_archive_bytes,
        bundle_max_extracted_bytes=resolved.bundle_max_extracted_bytes,
        bundle_max_member_count=resolved.bundle_max_member_count,
        operation_max_concurrent=resolved.operation_max_concurrent,
        operation_max_concurrent_active=resolved.operation_max_concurrent_active,
        restart_required_fields=list(resolved.restart_required_fields),
    )


def _audit(
    session: SessionDep,
    admin: User,
    event_type: str,
    changed_fields: list[str],
) -> None:
    AuditEventRepository(session).create(
        actor=admin,
        event_type=event_type,
        metadata={"changed_fields": sorted(changed_fields)},
    )


@router.get("", response_model=HarborSettingsResponse)
def get_harbor_settings(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    return _response(_service(request, session))


@router.patch("", response_model=HarborSettingsResponse)
def update_harbor_settings(
    payload: HarborSettingsPatch,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    service = _service(request, session)
    current = service.resolve()
    changed_fields: list[str] = []

    if "url" in payload.model_fields_set:
        new_url = str(payload.url).rstrip("/") if payload.url is not None else None
        if new_url != current.url:
            service.set_url(new_url)
            changed_fields.append("url")
    if "username" in payload.model_fields_set and payload.username != current.username:
        service.set_username(payload.username)
        changed_fields.append("username")
    if "verify_tls" in payload.model_fields_set and payload.verify_tls != current.verify_tls:
        service.set_verify_tls(bool(payload.verify_tls))
        changed_fields.append("verify_tls")

    if changed_fields:
        _audit(session, admin, "harbor.settings.updated", changed_fields)
    session.commit()
    return _response(service)


@router.put("/credential", response_model=HarborMutationResponse)
def rotate_harbor_credential(
    payload: HarborCredentialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    try:
        service.rotate_credential(payload.secret.get_secret_value())
    except HarborSettingsError as exc:
        raise _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message) from exc
    _audit(session, admin, "harbor.credential.rotated", ["credential"])
    session.commit()
    return HarborMutationResponse(changed_fields=["credential"])


@router.put("/ca", response_model=HarborMutationResponse)
def install_harbor_ca(
    payload: HarborCARequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    try:
        service.install_ca(payload.certificate_pem)
    except HarborSettingsError as exc:
        raise _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message) from exc
    _audit(session, admin, "harbor.ca.updated", ["custom_ca"])
    session.commit()
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.delete("/ca", response_model=HarborMutationResponse)
def remove_harbor_ca(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    service.remove_managed_ca()
    _audit(session, admin, "harbor.ca.removed", ["custom_ca"])
    session.commit()
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.post("/test", response_model=HarborConnectionTestResponse)
def test_harbor_connection(
    _admin: AdminDep,
    client: HarborClientDep,
) -> HarborConnectionTestResponse:
    try:
        info = client.system_info()
    except HarborClientError as exc:
        mapping = {
            "unauthorized": ("harbor_auth_failed", "Harbor отклонил учётные данные"),
            "forbidden": ("harbor_forbidden", "Harbor запретил доступ порталу"),
            "timeout": ("harbor_unavailable", "Harbor не ответил вовремя"),
            "tls_failed": ("harbor_tls_failed", "Не удалось проверить TLS-сертификат Harbor"),
            "connection_failed": ("harbor_unavailable", "Не удалось подключиться к Harbor"),
        }
        code, message = mapping.get(
            exc.code,
            ("harbor_error", "Проверка подключения к Harbor завершилась ошибкой"),
        )
        return HarborConnectionTestResponse(ok=False, code=code, message=message)
    return HarborConnectionTestResponse(
        ok=True,
        code="harbor_connection_ok",
        message="Подключение к локальному Harbor успешно",
        version=info.harbor_version,
    )


@transfer_router.get("", response_model=TransferSettingsResponse)
def get_transfer_settings(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> TransferSettingsResponse:
    try:
        return _transfer_response(_transfer_service(request, session))
    except TransferSettingsError as exc:
        raise _api_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            exc.code,
            exc.message,
        ) from exc


@transfer_router.patch("", response_model=TransferSettingsResponse)
def update_transfer_settings(
    payload: TransferSettingsPatch,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TransferSettingsResponse:
    service = _transfer_service(request, session)
    try:
        current = service.resolve()
        requested = payload.model_dump(exclude_unset=True)
        changed = {
            field: value
            for field, value in requested.items()
            if value != getattr(current, field)
        }
        if changed:
            service.set_values(changed)
            metadata = {
                "changed_fields": sorted(changed),
                "changes": {
                    field: {
                        "old": getattr(current, field),
                        "new": value,
                        "apply_mode": (
                            "restart_required"
                            if field in RESTART_REQUIRED_FIELDS
                            else "hot"
                        ),
                    }
                    for field, value in sorted(changed.items())
                },
            }
            AuditEventRepository(session).create(
                actor=admin,
                event_type="transfer.settings.updated",
                metadata=metadata,
            )
        session.commit()
        if changed:
            request.app.state.settings = service.effective_settings()
        return _transfer_response(service)
    except TransferSettingsError as exc:
        session.rollback()
        raise _api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.code,
            exc.message,
        ) from exc
