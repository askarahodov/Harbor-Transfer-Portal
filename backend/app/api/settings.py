from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.settings import (
    HarborCARequest,
    HarborConnectionTestResponse,
    HarborCredentialRequest,
    HarborMutationResponse,
    HarborProfileCreate,
    HarborProfilePatch,
    HarborProfileResponse,
    HarborProfilesResponse,
    HarborSettingsPatch,
    HarborSettingsResponse,
)
from app.services.harbor_client import HarborClientError
from app.services.harbor_profile_runtime import harbor_profile_boundary
from app.services.harbor_profiles import HarborProfile, HarborProfileService
from app.services.harbor_settings import (
    DEFAULT_HARBOR_PROFILE_ID,
    HarborSettingsError,
    HarborSettingsService,
)

router = APIRouter(prefix="/settings/harbor", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _service(request: Request, session: SessionDep) -> HarborSettingsService:
    return HarborSettingsService(session, request.app.state.settings)


def _response(service: HarborSettingsService) -> HarborSettingsResponse:
    resolved = service.resolve_profile(DEFAULT_HARBOR_PROFILE_ID)
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


def _profile_response(
    service: HarborProfileService,
    profile: HarborProfile,
) -> HarborProfileResponse:
    return HarborProfileResponse(
        id=profile.id,
        name=profile.name,
        url=profile.url,
        username=profile.username,
        verify_tls=profile.verify_tls,
        enabled=profile.enabled,
        credential_configured=service.credential_configured(profile),
        custom_ca_configured=service.custom_ca_configured(profile),
        is_default=profile.is_default,
        is_active=profile.id == service.active_profile_id(),
    )


def _profile_error(exc: HarborSettingsError) -> HTTPException:
    if exc.code == "harbor_profile_not_found":
        return _api_error(status.HTTP_404_NOT_FOUND, exc.code, exc.message)
    if exc.code in {
        "harbor_profile_name_conflict",
        "harbor_profile_default_protected",
        "harbor_profile_default_managed_elsewhere",
        "harbor_profile_disabled",
        "harbor_profile_active_protected",
        "harbor_profile_busy",
        "harbor_active_profile_invalid",
    }:
        return _api_error(status.HTTP_409_CONFLICT, exc.code, exc.message)
    return _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message)


def _audit_profile(
    session: SessionDep,
    admin: User,
    event_type: str,
    *,
    profile: HarborProfile,
    changed_fields: list[str] | None = None,
) -> None:
    metadata: dict[str, object] = {
        "profile_id": profile.id,
        "profile_name": profile.name,
    }
    if changed_fields is not None:
        metadata["changed_fields"] = sorted(changed_fields)
    AuditEventRepository(session).create(
        actor=admin,
        event_type=event_type,
        metadata=metadata,
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
    profiles = HarborProfileService(session, request.app.state.settings)
    changed_fields: list[str] = []
    try:
        with harbor_profile_boundary():
            profiles.require_mutation_safe(DEFAULT_HARBOR_PROFILE_ID)
            current = service.resolve_profile(DEFAULT_HARBOR_PROFILE_ID)

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
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return _response(service)


@router.put("/credential", response_model=HarborMutationResponse)
def rotate_harbor_credential(
    payload: HarborCredentialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    profiles = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profiles.require_mutation_safe(DEFAULT_HARBOR_PROFILE_ID)
            service.rotate_credential(payload.secret.get_secret_value())
            _audit(session, admin, "harbor.credential.rotated", ["credential"])
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return HarborMutationResponse(changed_fields=["credential"])


@router.put("/ca", response_model=HarborMutationResponse)
def install_harbor_ca(
    payload: HarborCARequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    profiles = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profiles.require_mutation_safe(DEFAULT_HARBOR_PROFILE_ID)
            service.install_ca(payload.certificate_pem)
            _audit(session, admin, "harbor.ca.updated", ["custom_ca"])
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.delete("/ca", response_model=HarborMutationResponse)
def remove_harbor_ca(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = _service(request, session)
    profiles = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profiles.require_mutation_safe(DEFAULT_HARBOR_PROFILE_ID)
            service.remove_managed_ca()
            _audit(session, admin, "harbor.ca.removed", ["custom_ca"])
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.post("/test", response_model=HarborConnectionTestResponse)
def test_harbor_connection(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> HarborConnectionTestResponse:
    profiles = HarborProfileService(session, request.app.state.settings)
    client = None
    try:
        client = profiles.build_client(DEFAULT_HARBOR_PROFILE_ID)
        info = client.system_info()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
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
    finally:
        if client is not None:
            client.close()
    return HarborConnectionTestResponse(
        ok=True,
        code="harbor_connection_ok",
        message="Подключение к Default Harbor успешно",
        version=info.harbor_version,
    )


@router.get("/profiles", response_model=HarborProfilesResponse)
def list_harbor_profiles(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> HarborProfilesResponse:
    service = HarborProfileService(session, request.app.state.settings)
    return HarborProfilesResponse(
        items=[_profile_response(service, profile) for profile in service.list_profiles()]
    )


@router.post("/profiles", response_model=HarborProfileResponse, status_code=status.HTTP_201_CREATED)
def create_harbor_profile(
    payload: HarborProfileCreate,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborProfileResponse:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        profile = service.create(
            name=payload.name,
            url=str(payload.url).rstrip("/"),
            username=payload.username,
            verify_tls=payload.verify_tls,
            enabled=payload.enabled,
        )
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    _audit_profile(session, admin, "harbor.profile.created", profile=profile)
    session.commit()
    return _profile_response(service, profile)


@router.put("/profiles/{profile_id}/activate", response_model=HarborProfileResponse)
def activate_harbor_profile(
    profile_id: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborProfileResponse:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            previous, active = service.activate(profile_id)
            if previous.id != active.id:
                AuditEventRepository(session).create(
                    actor=admin,
                    event_type="harbor.profile.activated",
                    metadata={
                        "previous_profile_id": previous.id,
                        "previous_profile_name": previous.name,
                        "active_profile_id": active.id,
                        "active_profile_name": active.name,
                    },
                )
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return _profile_response(service, active)


@router.patch("/profiles/{profile_id}", response_model=HarborProfileResponse)
def update_harbor_profile(
    profile_id: str,
    payload: HarborProfilePatch,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborProfileResponse:
    service = HarborProfileService(session, request.app.state.settings)
    changed_fields = sorted(payload.model_fields_set)
    try:
        with harbor_profile_boundary():
            profile = service.update(
                profile_id,
                name=payload.name if "name" in payload.model_fields_set else None,
                url=(
                    str(payload.url).rstrip("/")
                    if "url" in payload.model_fields_set and payload.url is not None
                    else None
                ),
                username=payload.username,
                username_set="username" in payload.model_fields_set,
                verify_tls=(
                    payload.verify_tls if "verify_tls" in payload.model_fields_set else None
                ),
                enabled=payload.enabled if "enabled" in payload.model_fields_set else None,
            )
            if changed_fields:
                _audit_profile(
                    session,
                    admin,
                    "harbor.profile.updated",
                    profile=profile,
                    changed_fields=changed_fields,
                )
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return _profile_response(service, profile)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_harbor_profile(
    profile_id: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> None:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profile = service.get(profile_id)
            service.delete(profile_id)
            _audit_profile(session, admin, "harbor.profile.deleted", profile=profile)
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc


@router.put("/profiles/{profile_id}/credential", response_model=HarborMutationResponse)
def rotate_harbor_profile_credential(
    profile_id: str,
    payload: HarborCredentialRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profile = service.get(profile_id)
            service.rotate_credential(profile_id, payload.secret.get_secret_value())
            _audit_profile(
                session,
                admin,
                "harbor.profile.credential.rotated",
                profile=profile,
                changed_fields=["credential"],
            )
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return HarborMutationResponse(changed_fields=["credential"])


@router.put("/profiles/{profile_id}/ca", response_model=HarborMutationResponse)
def install_harbor_profile_ca(
    profile_id: str,
    payload: HarborCARequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profile = service.get(profile_id)
            service.install_ca(profile_id, payload.certificate_pem)
            _audit_profile(
                session,
                admin,
                "harbor.profile.ca.updated",
                profile=profile,
                changed_fields=["custom_ca"],
            )
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.delete("/profiles/{profile_id}/ca", response_model=HarborMutationResponse)
def remove_harbor_profile_ca(
    profile_id: str,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborMutationResponse:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        with harbor_profile_boundary():
            profile = service.get(profile_id)
            service.remove_ca(profile_id)
            _audit_profile(
                session,
                admin,
                "harbor.profile.ca.removed",
                profile=profile,
                changed_fields=["custom_ca"],
            )
            session.commit()
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc
    return HarborMutationResponse(changed_fields=["custom_ca"])


@router.post("/profiles/{profile_id}/test", response_model=HarborConnectionTestResponse)
def test_harbor_profile_connection(
    profile_id: str,
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> HarborConnectionTestResponse:
    service = HarborProfileService(session, request.app.state.settings)
    try:
        client = service.build_client(profile_id)
    except HarborSettingsError as exc:
        raise _profile_error(exc) from exc

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
    finally:
        client.close()
    return HarborConnectionTestResponse(
        ok=True,
        code="harbor_connection_ok",
        message="Подключение к Harbor profile успешно",
        version=info.harbor_version,
    )
