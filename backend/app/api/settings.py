from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.harbor import HarborClientDep
from app.auth.dependencies import CurrentUserDep, SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.settings import (
    HarborCARequest,
    HarborConnectionTestResponse,
    HarborCredentialRequest,
    HarborMutationResponse,
    HarborProfileCreate,
    HarborProfileListResponse,
    HarborProfilePatch,
    HarborProfileResponse,
    HarborSettingsPatch,
    HarborSettingsResponse,
)
from app.services.harbor_client import HarborClientError
from app.services.harbor_settings import (
    HarborProfileInfo,
    HarborSettingsError,
    HarborSettingsService,
)

router = APIRouter(prefix="/settings/harbor", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _service(request: Request, session: SessionDep) -> HarborSettingsService:
    return HarborSettingsService(session, request.app.state.settings)


def _profile_error_status(exc: HarborSettingsError) -> int:
    return (
        status.HTTP_409_CONFLICT
        if exc.code == "harbor_profile_in_use"
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )


def _profile_response(profile: HarborProfileInfo) -> HarborProfileResponse:
    return HarborProfileResponse(
        id=profile.id,
        name=profile.name,
        url=profile.url,
        username=profile.username,
        verify_tls=profile.verify_tls,
        enabled=profile.enabled,
        credential_configured=profile.credential_configured,
        custom_ca_configured=profile.custom_ca_configured,
        legacy_default=profile.legacy_default,
    )


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


@router.get("/profiles", response_model=HarborProfileListResponse)
def list_harbor_profiles(
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
) -> HarborProfileListResponse:
    service = _service(request, session)
    return HarborProfileListResponse(
        items=[_profile_response(profile) for profile in service.list_profiles()]
    )


@router.post(
    "/profiles",
    response_model=HarborProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_harbor_profile(
    payload: HarborProfileCreate,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborProfileResponse:
    service = _service(request, session)
    try:
        profile = service.create_profile(
            name=payload.name,
            url=str(payload.url).rstrip("/"),
            username=payload.username,
            verify_tls=payload.verify_tls,
        )
    except HarborSettingsError as exc:
        raise _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message) from exc
    _audit(
        session,
        admin,
        "harbor.profile.created",
        ["name", "url", "username", "verify_tls"],
    )
    session.commit()
    return _profile_response(profile)


@router.patch("/profiles/{profile_id}", response_model=HarborProfileResponse)
def update_harbor_profile(
    profile_id: str,
    payload: HarborProfilePatch,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborProfileResponse:
    service = _service(request, session)
    kwargs: dict[str, object] = {}
    for field in ("name", "url", "username", "verify_tls", "enabled"):
        if field in payload.model_fields_set:
            value = getattr(payload, field)
            kwargs[field] = (
                str(value).rstrip("/")
                if field == "url" and value is not None
                else value
            )
    try:
        profile = service.update_profile(profile_id, **kwargs)
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
    if kwargs:
        _audit(session, admin, "harbor.profile.updated", list(kwargs))
    session.commit()
    return _profile_response(profile)


@router.delete("/profiles/{profile_id}", response_model=HarborMutationResponse)
def delete_harbor_profile(
    profile_id: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    try:
        service.delete_profile(profile_id)
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
    _audit(session, admin, "harbor.profile.deleted", ["profile"])
    session.commit()
    return HarborMutationResponse(changed_fields=["profile"])


@router.put("/profiles/{profile_id}/credential", response_model=HarborMutationResponse)
def rotate_harbor_profile_credential(
    profile_id: str,
    payload: HarborCredentialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    try:
        service.rotate_credential(payload.secret.get_secret_value(), profile_id)
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
    _audit(session, admin, "harbor.profile.credential.rotated", ["credential"])
    session.commit()
    return HarborMutationResponse(changed_fields=["credential"])


@router.put("/profiles/{profile_id}/ca", response_model=HarborMutationResponse)
def install_harbor_profile_ca(
    profile_id: str,
    payload: HarborCARequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    try:
        service.install_ca(payload.certificate_pem, profile_id)
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
    _audit(session, admin, "harbor.profile.ca.updated", ["custom_ca"])
    session.commit()
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.delete("/profiles/{profile_id}/ca", response_model=HarborMutationResponse)
def remove_harbor_profile_ca(
    profile_id: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    try:
        service.remove_managed_ca(profile_id)
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
    _audit(session, admin, "harbor.profile.ca.removed", ["custom_ca"])
    session.commit()
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.post("/profiles/{profile_id}/test", response_model=HarborConnectionTestResponse)
def test_harbor_profile_connection(
    profile_id: str,
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> HarborConnectionTestResponse:
    try:
        client = _service(request, session).build_client(profile_id)
    except HarborSettingsError as exc:
        return HarborConnectionTestResponse(ok=False, code=exc.code, message=exc.message)
    try:
        with client:
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
        message="Подключение к Harbor profile успешно",
        version=info.harbor_version,
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

    try:
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
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc

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
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
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
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
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
    try:
        service.remove_managed_ca()
    except HarborSettingsError as exc:
        raise _api_error(_profile_error_status(exc), exc.code, exc.message) from exc
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
