import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.harbor import HarborClientDep, api_error, harbor_http_error
from app.auth.dependencies import SessionDep, require_roles
from app.config import Settings
from app.db.models import User, UserRole
from app.schemas.settings import (
    HarborCaUpdateRequest,
    HarborConnectionTestResponse,
    HarborCredentialUpdateRequest,
    HarborSettingsResponse,
    HarborSettingsUpdateRequest,
)
from app.services.harbor_client import HarborClientError
from app.services.harbor_settings import (
    CA_MODE_NONE,
    CA_MODE_RUNTIME,
    HARBOR_CA_MODE_KEY,
    HARBOR_URL_KEY,
    HARBOR_USER_KEY,
    HARBOR_VERIFY_TLS_KEY,
    HarborSettingsError,
    ca_source,
    credential_configured,
    install_runtime_ca,
    record_harbor_audit,
    remove_runtime_ca,
    resolve_harbor_settings,
    rotate_harbor_credential,
    set_metadata,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/harbor", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _effective(session: SessionDep, bootstrap: Settings) -> Settings:
    try:
        return resolve_harbor_settings(session, bootstrap)
    except HarborSettingsError as exc:
        raise api_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code, exc.message) from exc


def _response(session: SessionDep, bootstrap: Settings) -> HarborSettingsResponse:
    settings = _effective(session, bootstrap)
    return HarborSettingsResponse(
        contour=bootstrap.portal_contour,
        url=str(settings.harbor_url) if settings.harbor_url is not None else None,
        username=settings.harbor_user,
        verify_tls=settings.harbor_verify_tls,
        credential_configured=credential_configured(settings),
        custom_ca_configured=settings.harbor_ca_file is not None,
        custom_ca_source=ca_source(settings, bootstrap),
    )


@router.get("", response_model=HarborSettingsResponse)
def get_harbor_settings(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    return _response(session, request.app.state.settings)


@router.patch("", response_model=HarborSettingsResponse)
def update_harbor_settings(
    payload: HarborSettingsUpdateRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    bootstrap: Settings = request.app.state.settings
    before = _effective(session, bootstrap)
    changed_fields: list[str] = []

    if "url" in payload.model_fields_set:
        new_url = str(payload.url) if payload.url is not None else None
        old_url = str(before.harbor_url) if before.harbor_url is not None else None
        if new_url != old_url:
            set_metadata(
                session,
                HARBOR_URL_KEY,
                new_url,
                description="Runtime URL локального Harbor",
            )
            changed_fields.append("harbor_url")

    if "username" in payload.model_fields_set:
        if payload.username != before.harbor_user:
            set_metadata(
                session,
                HARBOR_USER_KEY,
                payload.username,
                description="Runtime username локального Harbor",
            )
            changed_fields.append("harbor_user")

    if payload.verify_tls is not None and payload.verify_tls != before.harbor_verify_tls:
        set_metadata(
            session,
            HARBOR_VERIFY_TLS_KEY,
            payload.verify_tls,
            description="Runtime TLS verification policy локального Harbor",
        )
        changed_fields.append("harbor_verify_tls")
        if payload.verify_tls is False:
            logger.warning(
                "Harbor TLS certificate verification disabled by admin user_id=%s",
                admin.id,
            )

    if changed_fields:
        record_harbor_audit(
            session,
            actor=admin,
            event_type="harbor.settings.updated",
            changed_fields=changed_fields,
        )
        session.commit()

    return _response(session, bootstrap)


@router.put("/credential", response_model=HarborSettingsResponse)
def rotate_credential(
    payload: HarborCredentialUpdateRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    bootstrap: Settings = request.app.state.settings
    try:
        rotate_harbor_credential(bootstrap, payload.secret)
    except HarborSettingsError as exc:
        raise api_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code, exc.message) from exc

    record_harbor_audit(
        session,
        actor=admin,
        event_type="harbor.credential.rotated",
        changed_fields=["harbor_credential"],
    )
    session.commit()
    return _response(session, bootstrap)


@router.put("/ca", response_model=HarborSettingsResponse)
def install_ca(
    payload: HarborCaUpdateRequest,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    bootstrap: Settings = request.app.state.settings
    try:
        install_runtime_ca(bootstrap, payload.certificate_pem)
    except HarborSettingsError as exc:
        raise api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, exc.message) from exc

    set_metadata(
        session,
        HARBOR_CA_MODE_KEY,
        CA_MODE_RUNTIME,
        description="Runtime custom CA mode локального Harbor",
    )
    record_harbor_audit(
        session,
        actor=admin,
        event_type="harbor.ca.updated",
        changed_fields=["harbor_custom_ca"],
    )
    session.commit()
    return _response(session, bootstrap)


@router.delete("/ca", response_model=HarborSettingsResponse)
def clear_ca(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> HarborSettingsResponse:
    bootstrap: Settings = request.app.state.settings
    try:
        remove_runtime_ca(bootstrap)
    except HarborSettingsError as exc:
        raise api_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code, exc.message) from exc

    set_metadata(
        session,
        HARBOR_CA_MODE_KEY,
        CA_MODE_NONE,
        description="Runtime custom CA mode локального Harbor",
    )
    record_harbor_audit(
        session,
        actor=admin,
        event_type="harbor.ca.cleared",
        changed_fields=["harbor_custom_ca"],
    )
    session.commit()
    return _response(session, bootstrap)


@router.post("/test", response_model=HarborConnectionTestResponse)
def test_harbor_connection(
    _admin: AdminDep,
    client: HarborClientDep,
) -> HarborConnectionTestResponse:
    try:
        info = client.system_info()
    except HarborClientError as exc:
        raise harbor_http_error(exc) from exc
    return HarborConnectionTestResponse(
        connected=True,
        version=info.harbor_version,
        auth_mode=info.auth_mode,
    )
