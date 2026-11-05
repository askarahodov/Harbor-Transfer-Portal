"""Read-only exact Nexus Docker reference inspection without Harbor browse."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentUserDep, SessionDep
from app.services.harbor_profiles import HarborProfileService
from app.services.harbor_settings import HarborSettingsError
from app.services.nexus_discovery import NexusDiscoveryService
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
    if profile.registry_type != "nexus":
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
        # Never surface Skopeo command output, secrets or internal endpoints.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "registry_inspection_failed",
                "message": "Не удалось проверить Docker image. Проверьте tag и доступы.",
            },
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
) -> NexusSuggestionResponse:
    try:
        result = await NexusDiscoveryService(
            session, request.app.state.settings, profile_id
        ).catalog(search.strip())
    except HarborSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": "Выбранный Nexus profile недоступен"},
        ) from exc
    return NexusSuggestionResponse(
        available=result.available, items=list(result.items),
        message=result.message, truncated=result.truncated,
    )


@router.get("/nexus/tags", response_model=NexusSuggestionResponse)
async def nexus_tag_suggestions(
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
    profile_id: str = Query(min_length=1, max_length=64),
    repository: str = Query(min_length=1, max_length=255),
    search: str = Query(default="", max_length=128),
) -> NexusSuggestionResponse:
    try:
        result = await NexusDiscoveryService(
            session, request.app.state.settings, profile_id
        ).tags(repository, search.strip())
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
    )
