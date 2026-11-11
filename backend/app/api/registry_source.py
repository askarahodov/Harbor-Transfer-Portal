"""Read-only exact Nexus Docker reference inspection without Harbor browse."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentUserDep, SessionDep
from app.services.harbor_profiles import HarborProfileService
from app.services.harbor_settings import HarborSettingsError
from app.services.nexus_discovery import NexusDiscoveryService
from app.services.nexus_helm_http import NexusHelmHttpError, NexusHelmHttpService
from app.services.nexus_service import ImageReference, NexusService, NexusServiceError

router = APIRouter(prefix="/registry/source", tags=["registry"])


class ExactSourceInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=64)
    repository: str = Field(min_length=1, max_length=255)
    reference: str = Field(min_length=1, max_length=128)


class ExactSourceInspectionResponse(BaseModel):
    repository: str
    reference: str
    digest: str
    media_type: str | None = None


@router.post("/inspect", response_model=ExactSourceInspectionResponse)
async def inspect_exact_source(
    payload: ExactSourceInspectionRequest,
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
) -> ExactSourceInspectionResponse:
    """Inspect one explicitly provided repository:tag (no catalog privilege)."""
    try:
        image = ImageReference(repository=payload.repository, reference=payload.reference)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "registry_reference_invalid", "message": "Некорректный repository:tag"},
        ) from exc

    settings = request.app.state.settings
    profiles = HarborProfileService(session, settings)
    try:
        profile = profiles.operation_snapshot(payload.profile_id)
    except HarborSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "registry_profile_unavailable", "message": "Профиль недоступен"},
        ) from exc
    if profile.registry_type != "nexus" or getattr(profile, "nexus_format", "docker") != "docker":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "registry_type_mismatch",
                "message": "Требуется Nexus Docker v2 profile",
            },
        )

    try:
        inspected = await NexusService(
            session, settings, nexus_profile_id=profile.id
        ).inspect_image(image)
    except NexusServiceError as exc:
        # Return only fixed, typed errors. Skopeo stdout/stderr, private URLs
        # and service credentials must not cross the API boundary.
        errors = {
            "nexus_auth_failed": (
                status.HTTP_403_FORBIDDEN,
                "registry_auth_failed",
                "Nexus отклонил учётные данные или запретил доступ",
            ),
            "nexus_not_found": (
                status.HTTP_404_NOT_FOUND,
                "registry_image_not_found",
                "Docker image или tag не найден в доступном Nexus repository",
            ),
            "nexus_tls_failed": (
                status.HTTP_502_BAD_GATEWAY,
                "registry_tls_failed",
                "Невозможно проверить TLS-сертификат Nexus",
            ),
            "nexus_timeout": (
                status.HTTP_504_GATEWAY_TIMEOUT,
                "registry_timeout",
                "Истекло время ожидания Docker Registry",
            ),
            "nexus_registry_connection_failed": (
                status.HTTP_502_BAD_GATEWAY,
                "registry_unavailable",
                "Нет сетевого соединения с Docker Registry",
            ),
        }
        code_status, code, message = errors.get(
            exc.code,
            (
                status.HTTP_502_BAD_GATEWAY,
                "registry_inspection_failed",
                "Не удалось проверить Docker image. Проверьте tag и доступы.",
            ),
        )
        raise HTTPException(
            status_code=code_status,
            detail={"code": code, "message": message},
        ) from exc
    return ExactSourceInspectionResponse(
        repository=image.repository,
        reference=image.reference,
        digest=inspected.digest,
        media_type=inspected.media_type,
    )


class NexusSuggestionResponse(BaseModel):
    available: bool
    items: list[str]
    message: str | None = None
    truncated: bool = False
    next_cursor: str | None = None


class NexusRepositoryItemResponse(BaseModel):
    name: str
    format: str
    kind: str


class NexusInventoryResponse(BaseModel):
    available: bool
    items: list[NexusRepositoryItemResponse]
    message: str | None = None


@router.get("/nexus/inventory", response_model=NexusInventoryResponse)
async def nexus_repository_inventory(
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
    profile_id: str = Query(min_length=1, max_length=64),
) -> NexusInventoryResponse:
    try:
        result = await NexusDiscoveryService(
            session, request.app.state.settings, profile_id
        ).inventory()
    except HarborSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": "Выбранный Nexus profile недоступен"},
        ) from exc
    return NexusInventoryResponse(
        available=result.available,
        items=[
            NexusRepositoryItemResponse(name=item.name, format=item.format, kind=item.kind)
            for item in result.items
        ],
        message=result.message,
    )


@router.get("/nexus/repositories", response_model=NexusSuggestionResponse)
async def nexus_repository_suggestions(
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
    profile_id: str = Query(min_length=1, max_length=64),
    search: str = Query(default="", max_length=128),
    paged: bool = False,
    cursor: str | None = Query(default=None, max_length=512),
) -> NexusSuggestionResponse:
    try:
        service = NexusDiscoveryService(session, request.app.state.settings, profile_id)
        result = (
            await service.catalog_page(search.strip(), cursor)
            if paged else await service.catalog(search.strip())
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "registry_cursor_invalid", "message": "Неверный курсор Nexus"},
        ) from exc
    except HarborSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": "Выбранный Nexus profile недоступен"},
        ) from exc
    return NexusSuggestionResponse(
        available=result.available, items=list(result.items),
        message=result.message, truncated=result.truncated,
        next_cursor=result.next_cursor,
    )


@router.get("/nexus/tags", response_model=NexusSuggestionResponse)
async def nexus_tag_suggestions(
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
    profile_id: str = Query(min_length=1, max_length=64),
    repository: str = Query(min_length=1, max_length=255),
    search: str = Query(default="", max_length=128),
    paged: bool = False,
    cursor: str | None = Query(default=None, max_length=512),
) -> NexusSuggestionResponse:
    try:
        service = NexusDiscoveryService(session, request.app.state.settings, profile_id)
        result = (
            await service.tags_page(repository, search.strip(), cursor)
            if paged else await service.tags(repository, search.strip())
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "registry_reference_invalid", "message": "Неверный Docker repository"},
        ) from exc
    except HarborSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": "Выбранный Nexus profile недоступен"},
        ) from exc
    return NexusSuggestionResponse(
        available=result.available, items=list(result.items),
        message=result.message, truncated=result.truncated,
        next_cursor=result.next_cursor,
    )


class NexusHelmChartVersionResponse(BaseModel):
    name: str
    version: str
    digest: str


class NexusHelmCatalogResponse(BaseModel):
    available: bool
    items: list[NexusHelmChartVersionResponse]
    code: str | None = None
    message: str | None = None


@router.get("/nexus/helm/charts", response_model=NexusHelmCatalogResponse)
async def nexus_helm_catalog(
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
    profile_id: str = Query(min_length=1, max_length=64),
    repository: str = Query(min_length=1, max_length=128),
) -> NexusHelmCatalogResponse:
    """Read a selected classic Helm repository via index.yaml, never OCI."""
    try:
        result = await NexusHelmHttpService(
            session, request.app.state.settings, profile_id
        ).list_charts(repository)
    except NexusHelmHttpError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except HarborSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": "Выбранный Nexus profile недоступен"},
        ) from exc
    return NexusHelmCatalogResponse(
        available=result.available,
        items=[
            NexusHelmChartVersionResponse(
                name=item.name, version=item.version, digest=item.digest
            )
            for item in result.items
        ],
        code=result.code,
        message=result.message,
    )
