from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth.dependencies import CurrentUserDep
from app.schemas.harbor import (
    ArtifactKind,
    HarborArtifactResponse,
    HarborArtifactsPage,
    HarborConnectionResponse,
    HarborProjectResponse,
    HarborProjectsPage,
    HarborRepositoriesPage,
    HarborRepositoryResponse,
    PageResponse,
)
from app.services.harbor_client import HarborArtifact, HarborClient, HarborClientError

router = APIRouter(prefix="/harbor", tags=["harbor"])


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def get_harbor_client(request: Request) -> Generator[HarborClient, None, None]:
    try:
        client = HarborClient.from_settings(request.app.state.settings)
    except ValueError as exc:
        raise _api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "harbor_not_configured",
            "Локальный Harbor не настроен",
        ) from exc
    try:
        yield client
    finally:
        client.close()


HarborClientDep = Annotated[HarborClient, Depends(get_harbor_client)]


def _page[T](items: list[T], page: int, page_size: int) -> tuple[list[T], PageResponse]:
    start = (page - 1) * page_size
    end = start + page_size
    pagination = PageResponse(page=page, page_size=page_size, total=len(items))
    return items[start:end], pagination


def _matches(value: str, search: str | None) -> bool:
    return not search or search.casefold() in value.casefold()


def _repository_name(project: str, harbor_name: str) -> str:
    prefix = f"{project}/"
    if harbor_name.startswith(prefix):
        return harbor_name[len(prefix) :]
    return harbor_name


def _classify(artifact: HarborArtifact) -> ArtifactKind:
    artifact_type = (artifact.type or "").upper()
    haystack = " ".join(filter(None, [artifact.type, artifact.media_type])).casefold()
    annotations = artifact.extra_attrs.get("annotations") if artifact.extra_attrs else None
    if isinstance(annotations, dict):
        haystack += " " + " ".join(str(value).casefold() for value in annotations.values())

    if artifact_type in {"CHART", "HELM", "HELM_CHART"} or "helm" in haystack:
        return ArtifactKind.HELM_CHART
    if artifact_type == "IMAGE" or "image" in haystack or "container" in haystack:
        return ArtifactKind.CONTAINER_IMAGE
    return ArtifactKind.UNKNOWN_OCI


def _artifact_response(
    project: str,
    repository: str,
    artifact: HarborArtifact,
) -> HarborArtifactResponse:
    references = sorted({tag.name for tag in artifact.tags if tag.name}, key=str.casefold)
    return HarborArtifactResponse(
        kind=_classify(artifact),
        project=project,
        repository=repository,
        references=references,
        digest=artifact.digest,
        size=artifact.size,
        pushed_at=artifact.push_time,
        media_type=artifact.media_type,
        artifact_type=artifact.type,
    )


def _harbor_error(exc: HarborClientError) -> HTTPException:
    mapping = {
        "unauthorized": (
            status.HTTP_502_BAD_GATEWAY,
            "harbor_auth_failed",
            "Локальный Harbor отклонил учётные данные портала",
        ),
        "forbidden": (
            status.HTTP_502_BAD_GATEWAY,
            "harbor_forbidden",
            "Локальный Harbor запретил доступ порталу",
        ),
        "not_found": (
            status.HTTP_404_NOT_FOUND,
            "harbor_not_found",
            "Запрошенный ресурс не найден в локальном Harbor",
        ),
        "timeout": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "harbor_unavailable",
            "Локальный Harbor не ответил вовремя",
        ),
        "connection_failed": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "harbor_unavailable",
            "Не удалось подключиться к локальному Harbor",
        ),
        "harbor_unavailable": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "harbor_unavailable",
            "Локальный Harbor временно недоступен",
        ),
        "rate_limited": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "harbor_rate_limited",
            "Локальный Harbor временно ограничил частоту запросов",
        ),
        "invalid_response": (
            status.HTTP_502_BAD_GATEWAY,
            "harbor_invalid_response",
            "Локальный Harbor вернул некорректный ответ",
        ),
    }
    status_code, code, message = mapping.get(
        exc.code,
        (
            status.HTTP_502_BAD_GATEWAY,
            "harbor_error",
            "Ошибка обращения к локальному Harbor",
        ),
    )
    return _api_error(status_code, code, message)


@router.get("/connection", response_model=HarborConnectionResponse)
def connection(_user: CurrentUserDep, client: HarborClientDep) -> HarborConnectionResponse:
    try:
        info = client.system_info()
    except HarborClientError as exc:
        raise _harbor_error(exc) from exc
    return HarborConnectionResponse(
        connected=True,
        version=info.harbor_version,
        auth_mode=info.auth_mode,
    )


@router.get("/projects", response_model=HarborProjectsPage)
def projects(
    _user: CurrentUserDep,
    client: HarborClientDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=256),
) -> HarborProjectsPage:
    try:
        items = [
            HarborProjectResponse(name=item.name, public=item.public)
            for item in client.list_projects()
            if _matches(item.name, search)
        ]
    except HarborClientError as exc:
        raise _harbor_error(exc) from exc

    items.sort(key=lambda item: item.name.casefold())
    page_items, pagination = _page(items, page, page_size)
    return HarborProjectsPage(pagination=pagination, items=page_items)


@router.get(
    "/projects/{project}/repositories",
    response_model=HarborRepositoriesPage,
)
def repositories(
    project: str,
    _user: CurrentUserDep,
    client: HarborClientDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=256),
) -> HarborRepositoriesPage:
    try:
        items = []
        for item in client.list_repositories(project):
            repository = _repository_name(project, item.name)
            if _matches(repository, search):
                items.append(
                    HarborRepositoryResponse(
                        name=repository,
                        artifact_count=item.artifact_count,
                        pull_count=item.pull_count,
                    )
                )
    except HarborClientError as exc:
        raise _harbor_error(exc) from exc

    items.sort(key=lambda item: item.name.casefold())
    page_items, pagination = _page(items, page, page_size)
    return HarborRepositoriesPage(pagination=pagination, items=page_items)


@router.get(
    "/projects/{project}/artifacts",
    response_model=HarborArtifactsPage,
)
def artifacts(
    project: str,
    repository: str,
    _user: CurrentUserDep,
    client: HarborClientDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=256),
) -> HarborArtifactsPage:
    try:
        normalized = [
            _artifact_response(project, repository, artifact)
            for artifact in client.list_artifacts(project, repository)
        ]
    except HarborClientError as exc:
        raise _harbor_error(exc) from exc

    if search:
        needle = search.casefold()
        normalized = [
            item
            for item in normalized
            if needle in item.digest.casefold()
            or any(needle in reference.casefold() for reference in item.references)
        ]
    normalized.sort(
        key=lambda item: (
            not item.references,
            item.references[0].casefold() if item.references else "",
            item.digest,
        )
    )
    page_items, pagination = _page(normalized, page, page_size)
    return HarborArtifactsPage(pagination=pagination, items=page_items)
