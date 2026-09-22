import re
from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth.dependencies import CurrentUserDep, SessionDep
from app.domain.artifacts import classify_artifact_kind
from app.schemas.harbor import (
    ArtifactKind,
    HarborArtifactResponse,
    HarborArtifactsPage,
    HarborConnectionResponse,
    HarborProjectResponse,
    HarborProjectsPage,
    HarborRepositoriesPage,
    HarborRepositoryResponse,
    HarborSelectableProfileResponse,
    HarborSelectableProfilesResponse,
    PageResponse,
)
from app.services.harbor_client import HarborArtifact, HarborClient, HarborClientError
from app.services.harbor_profiles import DEFAULT_PROFILE_ID, HarborProfileService
from app.services.harbor_settings import HarborSettingsError

router = APIRouter(prefix="/harbor", tags=["harbor"])

_SEARCH_SCAN_PAGE_SIZE = 100
_SEARCH_SCAN_MAX_PAGES = 20
_TOKEN_SPLIT = re.compile(r"[\s/_.:-]+")
_VERSION_LIKE = re.compile(r"^[vV]?\d+(?:\.\d+)+(?:[-+._A-Za-z0-9]*)?$")
_HEX_PREFIX = re.compile(r"^[0-9a-fA-F]{4,64}$")


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def get_harbor_client(
    request: Request,
    session: SessionDep,
    profile_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> Generator[HarborClient, None, None]:
    try:
        service = HarborProfileService(session, request.app.state.settings)
        selected_id = profile_id or service.active_profile_id()
        client = service.build_client(selected_id)
    except HarborSettingsError as exc:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "harbor_profile_not_found"
            else status.HTTP_409_CONFLICT
            if exc.code == "harbor_profile_disabled"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise _api_error(status_code, exc.code, exc.message) from exc
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


def _pagination(page: int, page_size: int, total: int) -> PageResponse:
    return PageResponse(page=page, page_size=page_size, total=total)


def _search_tokens(search: str) -> tuple[str, ...]:
    return tuple(
        token.casefold()
        for token in _TOKEN_SPLIT.split(search.strip())
        if token
    )


def _direct_fuzzy_search(search: str) -> bool:
    return bool(_VERSION_LIKE.fullmatch(search.strip())) or len(_search_tokens(search)) <= 1


def _upstream_needle(search: str) -> str:
    stripped = search.strip()
    if _VERSION_LIKE.fullmatch(stripped):
        return stripped
    tokens = _search_tokens(stripped)
    if not tokens:
        return stripped
    return max(tokens, key=len)


def _matches_tokens(values: tuple[str, ...], search: str) -> bool:
    tokens = _search_tokens(search)
    if not tokens:
        return True
    normalized = " ".join(
        " ".join(part for part in _TOKEN_SPLIT.split(value.casefold()) if part)
        for value in values
    )
    return all(token in normalized for token in tokens)


def _digest_search(search: str) -> bool:
    value = search.strip()
    return value.casefold().startswith("sha256:") or bool(_HEX_PREFIX.fullmatch(value))


def _repository_name(project: str, harbor_name: str) -> str:
    prefix = f"{project}/"
    if harbor_name.startswith(prefix):
        return harbor_name[len(prefix) :]
    return harbor_name


def _classify(artifact: HarborArtifact) -> ArtifactKind:
    extra_attrs = dict(artifact.extra_attrs)
    if artifact.artifact_type:
        extra_attrs["artifact_type"] = artifact.artifact_type
    if artifact.manifest_media_type:
        extra_attrs["manifest_media_type"] = artifact.manifest_media_type
    if artifact.annotations:
        extra_attrs["annotations"] = artifact.annotations
    return classify_artifact_kind(
        artifact.type,
        artifact.media_type,
        extra_attrs,
    )


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
        "tls_failed": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "harbor_tls_failed",
            "Не удалось проверить TLS-сертификат локального Harbor",
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


@router.get("/profiles", response_model=HarborSelectableProfilesResponse)
def selectable_profiles(
    _user: CurrentUserDep,
    request: Request,
    session: SessionDep,
) -> HarborSelectableProfilesResponse:
    service = HarborProfileService(session, request.app.state.settings)
    return HarborSelectableProfilesResponse(
        items=[
            HarborSelectableProfileResponse(
                id=profile.id,
                name=profile.name,
                url=profile.url,
                is_default=profile.is_default,
            )
            for profile in service.selectable_profiles()
        ]
    )


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


def _search_projects(client: HarborClient, search: str) -> list[HarborProjectResponse]:
    needle = _upstream_needle(search)
    matches: list[HarborProjectResponse] = []
    for upstream_page in range(1, _SEARCH_SCAN_MAX_PAGES + 1):
        result = client.list_projects_page(
            upstream_page,
            _SEARCH_SCAN_PAGE_SIZE,
            search_needle=needle,
        )
        matches.extend(
            HarborProjectResponse(name=item.name, public=item.public)
            for item in result.items
            if _matches_tokens((item.name,), search)
        )
        if upstream_page * _SEARCH_SCAN_PAGE_SIZE >= result.total:
            break
    matches.sort(key=lambda item: item.name.casefold())
    return matches


@router.get("/projects", response_model=HarborProjectsPage)
def projects(
    _user: CurrentUserDep,
    client: HarborClientDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=256),
) -> HarborProjectsPage:
    try:
        if search and search.strip() and not _direct_fuzzy_search(search):
            items, pagination = _page(_search_projects(client, search), page, page_size)
            return HarborProjectsPage(pagination=pagination, items=items)
        result = client.list_projects_page(
            page,
            page_size,
            search_needle=search.strip() if search and search.strip() else None,
        )
    except (HarborClientError, ValueError) as exc:
        if isinstance(exc, HarborClientError):
            raise _harbor_error(exc) from exc
        raise _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_search", str(exc)) from exc

    items = [HarborProjectResponse(name=item.name, public=item.public) for item in result.items]
    return HarborProjectsPage(
        pagination=_pagination(page, page_size, result.total),
        items=items,
    )


def _search_repositories(
    client: HarborClient,
    project: str,
    search: str,
) -> list[HarborRepositoryResponse]:
    needle = _upstream_needle(search)
    matches: list[HarborRepositoryResponse] = []
    for upstream_page in range(1, _SEARCH_SCAN_MAX_PAGES + 1):
        result = client.list_repositories_page(
            project,
            upstream_page,
            _SEARCH_SCAN_PAGE_SIZE,
            search_needle=needle,
        )
        for item in result.items:
            repository = _repository_name(project, item.name)
            if _matches_tokens((repository,), search):
                matches.append(
                    HarborRepositoryResponse(
                        name=repository,
                        artifact_count=item.artifact_count,
                        pull_count=item.pull_count,
                    )
                )
        if upstream_page * _SEARCH_SCAN_PAGE_SIZE >= result.total:
            break
    matches.sort(key=lambda item: item.name.casefold())
    return matches


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
        if search and search.strip() and not _direct_fuzzy_search(search):
            items, pagination = _page(
                _search_repositories(client, project, search),
                page,
                page_size,
            )
            return HarborRepositoriesPage(pagination=pagination, items=items)
        result = client.list_repositories_page(
            project,
            page,
            page_size,
            search_needle=search.strip() if search and search.strip() else None,
        )
    except (HarborClientError, ValueError) as exc:
        if isinstance(exc, HarborClientError):
            raise _harbor_error(exc) from exc
        raise _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_search", str(exc)) from exc

    items = [
        HarborRepositoryResponse(
            name=_repository_name(project, item.name),
            artifact_count=item.artifact_count,
            pull_count=item.pull_count,
        )
        for item in result.items
    ]
    return HarborRepositoriesPage(
        pagination=_pagination(page, page_size, result.total),
        items=items,
    )


def _artifact_matches(item: HarborArtifactResponse, search: str) -> bool:
    return _matches_tokens((item.digest, *item.references), search)


def _search_artifacts(
    client: HarborClient,
    project: str,
    repository: str,
    search: str,
) -> list[HarborArtifactResponse]:
    needle = _upstream_needle(search)
    digest_search = _digest_search(needle)
    matches: list[HarborArtifactResponse] = []
    for upstream_page in range(1, _SEARCH_SCAN_MAX_PAGES + 1):
        result = client.list_artifacts_page(
            project,
            repository,
            upstream_page,
            _SEARCH_SCAN_PAGE_SIZE,
            search_needle=needle,
            search_digest=digest_search,
        )
        normalized = [
            _artifact_response(project, repository, artifact)
            for artifact in result.items
        ]
        matches.extend(item for item in normalized if _artifact_matches(item, search))
        if upstream_page * _SEARCH_SCAN_PAGE_SIZE >= result.total:
            break
    matches.sort(
        key=lambda item: (
            not item.references,
            item.references[0].casefold() if item.references else "",
            item.digest,
        )
    )
    return matches


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
        if search and search.strip() and not _direct_fuzzy_search(search):
            items, pagination = _page(
                _search_artifacts(client, project, repository, search),
                page,
                page_size,
            )
            return HarborArtifactsPage(pagination=pagination, items=items)
        query = search.strip() if search and search.strip() else None
        result = client.list_artifacts_page(
            project,
            repository,
            page,
            page_size,
            search_needle=query,
            search_digest=bool(query and _digest_search(query)),
        )
    except (HarborClientError, ValueError) as exc:
        if isinstance(exc, HarborClientError):
            raise _harbor_error(exc) from exc
        raise _api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_search", str(exc)) from exc

    normalized = [
        _artifact_response(project, repository, artifact)
        for artifact in result.items
    ]
    return HarborArtifactsPage(
        pagination=_pagination(page, page_size, result.total),
        items=normalized,
    )
