"""Optional, read-only Docker Registry v2 discovery for Nexus SOURCE.

The Docker catalog is not guaranteed (especially with Nexus groups and restricted
identities). Callers must keep exact repository:tag inspection available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_profiles import HarborProfileService
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService

_REPOSITORY = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
)
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
_NEXUS_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True)
class DiscoveryResult:
    available: bool
    items: tuple[str, ...]
    message: str | None = None
    truncated: bool = False
    next_cursor: str | None = None


@dataclass(frozen=True)
class NexusRepositoryInfo:
    name: str
    format: str
    kind: str


@dataclass(frozen=True)
class NexusRepositoryInventory:
    available: bool
    items: tuple[NexusRepositoryInfo, ...]
    message: str | None = None


class NexusDiscoveryService:
    def __init__(self, session: Session, settings: Settings, profile_id: str) -> None:
        profile = HarborProfileService(session, settings).operation_snapshot(profile_id)
        if profile.registry_type != "nexus":
            raise HarborSettingsError("registry_type_mismatch", "Требуется Nexus Docker v2")
        effective = HarborSettingsService(session, settings).resolve_profile(profile.id)
        url = effective.url or ""
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or parsed.username or parsed.password
        ):
            raise HarborSettingsError("nexus_url_invalid", "Нужен корневой URL Docker v2 connector")
        self.url = url.rstrip("/")
        self.search_url = profile.nexus_search_url
        self.search_repository = profile.nexus_search_repository
        self.username = effective.username
        self.password = effective.password
        self.verify: bool | str = (
            str(effective.ca_file)
            if effective.verify_tls and effective.ca_file is not None
            else effective.verify_tls
        )
        self.timeout = httpx.Timeout(
            settings.nexus_read_timeout_seconds,
            connect=settings.nexus_connect_timeout_seconds,
        )

    async def inventory(self) -> NexusRepositoryInventory:
        """List only Docker/Helm repositories visible to the service identity.

        Visibility does not establish read, pull, push or upload permissions.
        Nexus may hide repositories from this API based on RBAC.
        """
        if not self.search_url:
            return NexusRepositoryInventory(
                False, (), "Для списка репозиториев нужен Nexus UI HTTPS URL"
            )
        parsed = urlsplit(self.search_url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            return NexusRepositoryInventory(False, (), "Нужен корневой Nexus UI HTTPS URL")
        auth = (
            httpx.BasicAuth(self.username, self.password or "")
            if self.username else None
        )
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self.timeout,
                follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.get(
                    self.search_url.rstrip("/") + "/service/rest/v1/repositories",
                    auth=auth,
                )
                if response.status_code in {401, 403}:
                    return NexusRepositoryInventory(
                        False, (), "Нет прав на список репозиториев Nexus"
                    )
                if response.status_code != 200:
                    return NexusRepositoryInventory(
                        False, (), "Не удалось получить список репозиториев Nexus"
                    )
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return NexusRepositoryInventory(False, (), "Список репозиториев Nexus недоступен")
        if not isinstance(payload, list):
            return NexusRepositoryInventory(False, (), "Некорректный ответ Nexus repositories API")
        found: dict[str, NexusRepositoryInfo] = {}
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            name, fmt, kind = (entry.get(key) for key in ("name", "format", "type"))
            if (
                not isinstance(name, str) or _NEXUS_REPO.fullmatch(name) is None
                or not isinstance(fmt, str) or fmt not in {"docker", "helm"}
                or not isinstance(kind, str) or kind not in {"hosted", "proxy", "group"}
            ):
                continue
            found[name] = NexusRepositoryInfo(name, fmt, kind)
        items = tuple(sorted(found.values(), key=lambda item: (item.format, item.name)))
        return NexusRepositoryInventory(
            True, items[:200],
            "Список ограничен 200 записями" if len(items) > 200 else None,
        )

    async def catalog(self, search: str = "") -> DiscoveryResult:
        if self.search_url and self.search_repository:
            return await self._search_rest(None, search)
        return await self._get("/v2/_catalog", "repositories", search, _REPOSITORY)

    async def tags(self, repository: str, search: str = "") -> DiscoveryResult:
        if _REPOSITORY.fullmatch(repository) is None:
            raise ValueError("invalid Docker repository")
        if self.search_url and self.search_repository:
            return await self._search_rest(repository, search)
        escaped = "/".join(quote(piece, safe="") for piece in repository.split("/"))
        return await self._get(f"/v2/{escaped}/tags/list", "tags", search, _TAG)

    async def catalog_page(
        self, search: str = "", cursor: str | None = None,
    ) -> DiscoveryResult:
        """One bounded page, with continuation explicitly exposed to the UI."""
        return await self._browse_page(None, search, cursor)

    async def tags_page(
        self, repository: str, search: str = "", cursor: str | None = None,
    ) -> DiscoveryResult:
        if _REPOSITORY.fullmatch(repository) is None:
            raise ValueError("invalid Docker repository")
        return await self._browse_page(repository, search, cursor)

    async def _browse_page(
        self, repository: str | None, search: str, cursor: str | None,
    ) -> DiscoveryResult:
        rest = bool(self.search_url and self.search_repository)
        if cursor is not None and (
            not cursor or len(cursor) > 512
            or (not rest and (
                _REPOSITORY.fullmatch(cursor) if repository is None
                else _TAG.fullmatch(cursor)
            ) is None)
        ):
            raise ValueError("invalid Nexus catalog cursor")
        if rest:
            return await self._rest_page(repository, search, cursor)
        return await self._docker_page(repository, search, cursor)

    async def _rest_page(
        self, repository: str | None, search: str, cursor: str | None,
    ) -> DiscoveryResult:
        if not self.search_url or not self.search_repository:
            return DiscoveryResult(False, (), "Nexus REST Search API не настроен")
        try:
            parsed = urlsplit(self.search_url)
            valid = (
                parsed.scheme == "https" and parsed.hostname is not None
                and parsed.port != 0 and parsed.path in {"", "/"}
                and not parsed.query and not parsed.fragment
                and parsed.username is None and parsed.password is None
            )
        except ValueError:
            valid = False
        if not valid or _NEXUS_REPO.fullmatch(self.search_repository) is None:
            return DiscoveryResult(
                False, (), "Некорректный Nexus REST Search HTTPS URL или repository"
            )
        auth = httpx.BasicAuth(self.username, self.password or "") if self.username else None
        params = {"repository": self.search_repository, "format": "docker"}
        # Exact image name is a Nexus-native search criterion: enumerate only
        # this image's versions, not all versions of a large proxy repository.
        if repository is not None:
            params["name"] = repository
        if cursor is not None:
            params["continuationToken"] = cursor
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self.timeout,
                follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.get(
                    self.search_url.rstrip("/") + "/service/rest/v1/search",
                    params=params, auth=auth,
                )
                if response.status_code != 200:
                    return DiscoveryResult(
                        False, (), "REST Search недоступен; используйте точный repository:tag"
                    )
                data = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return DiscoveryResult(False, (), "Не удалось получить страницу Nexus REST Search")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return DiscoveryResult(False, (), "Некорректный ответ Nexus REST Search API")
        rows = data["items"]
        if len(rows) > 1000:
            return DiscoveryResult(False, (), "Слишком большая страница Nexus REST Search")
        found: set[str] = set()
        for item in rows:
            if not isinstance(item, dict) or item.get("repository") != self.search_repository:
                continue
            name, version = item.get("name"), item.get("version")
            if not isinstance(name, str) or _REPOSITORY.fullmatch(name) is None:
                continue
            if repository is None:
                if search.casefold() in name.casefold():
                    found.add(name)
            elif name == repository and isinstance(version, str):
                if _TAG.fullmatch(version) and search.casefold() in version.casefold():
                    found.add(version)
        next_token = data.get("continuationToken")
        if next_token in (None, ""):
            next_token = None
        elif (
            not isinstance(next_token, str)
            or len(next_token) > 512
            or next_token == cursor
        ):
            return DiscoveryResult(False, (), "Некорректная пагинация Nexus REST Search")
        return DiscoveryResult(
            True, tuple(sorted(found)), truncated=next_token is not None,
            next_cursor=next_token,
        )

    async def _docker_page(
        self, repository: str | None, search: str, cursor: str | None,
    ) -> DiscoveryResult:
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname is not None and parsed.port != 0
                and parsed.path in {"", "/"}
                and not parsed.query and not parsed.fragment
                and parsed.username is None and parsed.password is None
            )
        except ValueError:
            valid = False
        if not valid or (self.username and parsed.scheme != "https"):
            return DiscoveryResult(False, (), "Нужен корректный HTTPS Docker v2 connector")
        path = (
            "/v2/_catalog" if repository is None
            else "/v2/" + "/".join(quote(x, safe="") for x in repository.split("/")) + "/tags/list"
        )
        field = "repositories" if repository is None else "tags"
        pattern = _REPOSITORY if repository is None else _TAG
        params: dict[str, str | int] = {"n": 100}
        if cursor is not None:
            params["last"] = cursor
        auth = httpx.BasicAuth(self.username, self.password or "") if self.username else None
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self.timeout,
                follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.get(self.url + path, params=params, auth=auth)
                if response.status_code != 200:
                    return DiscoveryResult(
                        False, (), "Nexus не разрешает перечисление; используйте repository:tag"
                    )
                data = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return DiscoveryResult(False, (), "Каталог Nexus недоступен")
        if not isinstance(data, dict) or not isinstance(data.get(field), list):
            return DiscoveryResult(False, (), "Nexus вернул некорректный каталог")
        values = data[field]
        if len(values) > 1000 or any(
            not isinstance(v, str) or pattern.fullmatch(v) is None for v in values
        ):
            return DiscoveryResult(False, (), "Nexus вернул некорректные имена в каталоге")
        if cursor is not None and values and values[-1] <= cursor:
            return DiscoveryResult(False, (), "Nexus вернул повторяющуюся страницу каталога")
        has_more = bool(values) and (
            "link" in response.headers or len(values) >= 100
        )
        next_cursor = values[-1] if has_more else None
        found = tuple(sorted({
            value for value in values if search.casefold() in value.casefold()
        }))
        return DiscoveryResult(
            True, found, truncated=next_cursor is not None,
            next_cursor=next_cursor,
        )

    async def _search_rest(self, repository: str | None, search: str) -> DiscoveryResult:
        """Read Nexus UI Search API for one configured Docker hosted repository.

        REST search endpoints are not the Docker connector. This opt-in admin
        configuration keeps credentials off arbitrary user-supplied URLs.
        """
        if not self.search_url or not self.search_repository:
            return DiscoveryResult(False, (), "Nexus REST Search API не настроен")
        try:
            parsed_search_url = urlsplit(self.search_url)
            valid_search_url = (
                parsed_search_url.scheme == "https"
                and parsed_search_url.hostname is not None
                and parsed_search_url.port != 0
                and parsed_search_url.path in {"", "/"}
                and not parsed_search_url.query
                and not parsed_search_url.fragment
                and parsed_search_url.username is None
                and parsed_search_url.password is None
            )
        except ValueError:
            valid_search_url = False
        if not valid_search_url:
            return DiscoveryResult(
                False, (), "Nexus REST Search требует корректный HTTPS URL"
            )
        if _NEXUS_REPO.fullmatch(self.search_repository) is None:
            return DiscoveryResult(False, (), "Некорректный Nexus Docker repository")
        auth = (
            httpx.BasicAuth(self.username, self.password or "")
            if self.username else None
        )
        found: set[str] = set()
        token: str | None = None
        seen_tokens: set[str] = set()
        truncated = False
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self.timeout,
                follow_redirects=False, trust_env=False,
            ) as client:
                for page in range(5):
                    params = {"repository": self.search_repository, "format": "docker"}
                    if repository is not None:
                        params["name"] = repository
                    if token:
                        params["continuationToken"] = token
                    response = await client.get(
                        self.search_url.rstrip("/") + "/service/rest/v1/search",
                        params=params, auth=auth,
                    )
                    if response.status_code != 200:
                        return DiscoveryResult(
                            False, (),
                            "REST Search недоступен; проверьте права или введите repository:tag",
                        )
                    data = response.json()
                    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                        return DiscoveryResult(
                            False, (), "Некорректный ответ Nexus REST Search API"
                        )
                    for item in data["items"]:
                        if not isinstance(item, dict):
                            continue
                        name = item.get("name")
                        version = item.get("version")
                        if (
                            item.get("repository") != self.search_repository
                            or not isinstance(name, str)
                            or _REPOSITORY.fullmatch(name) is None
                        ):
                            continue
                        if repository is None:
                            if search.casefold() in name.casefold():
                                found.add(name)
                        elif name == repository and isinstance(version, str):
                            if _TAG.fullmatch(version) and search.casefold() in version.casefold():
                                found.add(version)
                    next_token = data.get("continuationToken")
                    if next_token is None or next_token == "":
                        break
                    if (
                        not isinstance(next_token, str)
                        or len(next_token) > 512
                        or next_token in seen_tokens
                    ):
                        return DiscoveryResult(
                            False, (), "Некорректная пагинация Nexus REST Search"
                        )
                    seen_tokens.add(next_token)
                    token = next_token
                    if page == 4:
                        truncated = True
        except (httpx.HTTPError, ValueError, TypeError):
            return DiscoveryResult(
                False, (), "Не удалось получить Nexus REST Search; точный repository:tag доступен"
            )
        return DiscoveryResult(
            True, tuple(sorted(found)[:100]), truncated=truncated or len(found) > 100
        )

    async def _get(
        self, path: str, field: str, search: str, pattern: re.Pattern[str]
    ) -> DiscoveryResult:
        """Scan bounded Docker v2 pagination rather than filtering page one.

        The Docker registry catalog has no server-side substring search. The
        `last` cursor is read only from the validated result names; never
        follow an untrusted Link URL or redirect registry credentials.
        """
        if self.username and urlsplit(self.url).scheme != "https":
            return DiscoveryResult(
                False, (), "Nexus Docker v2 browse с учётными данными требует HTTPS"
            )
        auth = (
            httpx.BasicAuth(self.username, self.password or "")
            if self.username else None
        )
        matches: set[str] = set()
        last: str | None = None
        # Cap fan-out and response data: browsing is optional and must not
        # consume unbounded registry resources, especially through a group.
        max_pages = 20
        page_size = 100
        try:
            async with httpx.AsyncClient(
                verify=self.verify,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for page in range(max_pages):
                    params: dict[str, str | int] = {"n": page_size}
                    if last is not None:
                        params["last"] = last
                    response = await client.get(self.url + path, params=params, auth=auth)
                    if response.status_code != 200:
                        return DiscoveryResult(
                            False, (),
                            "Nexus не разрешает перечисление; используйте точный repository:tag",
                        )
                    try:
                        data = response.json()
                    except ValueError:
                        return DiscoveryResult(False, (), "Nexus вернул некорректный каталог")
                    if not isinstance(data, dict) or not isinstance(data.get(field), list):
                        return DiscoveryResult(False, (), "Nexus вернул некорректный каталог")
                    values = data[field]
                    if any(not isinstance(v, str) for v in values):
                        return DiscoveryResult(False, (), "Nexus вернул некорректный каталог")
                    valid = [value for value in values if pattern.fullmatch(value) is not None]
                    matches.update(
                        value for value in valid if search.casefold() in value.casefold()
                    )
                    if not valid:
                        if values:
                            return DiscoveryResult(
                                False, (), "Nexus вернул некорректные имена в каталоге"
                            )
                        return DiscoveryResult(True, tuple(sorted(matches)[:100]))

                    # RFC pagination is optional; 'last' is defined by the final
                    # returned entry, not by an arbitrary URL from the server.
                    next_last = values[-1]
                    has_more = "link" in response.headers or len(values) >= page_size
                    if not has_more:
                        return DiscoveryResult(
                            True, tuple(sorted(matches)[:100]),
                            truncated=len(matches) > 100,
                        )
                    if last is not None and next_last <= last:
                        return DiscoveryResult(
                            False, (), "Nexus вернул некорректную пагинацию каталога"
                        )
                    if page == max_pages - 1 or len(matches) >= 100:
                        return DiscoveryResult(
                            True, tuple(sorted(matches)[:100]),
                            message="Поиск ограничен первыми 2000 записями Docker v2; "
                            "для полного каталога настройте Nexus REST Search API",
                            truncated=True,
                        )
                    last = next_last
        except (httpx.TransportError, httpx.InvalidURL, ValueError):
            return DiscoveryResult(
                False, (), "Каталог Nexus недоступен; используйте точный repository:tag"
            )
        return DiscoveryResult(True, tuple(sorted(matches)[:100]), truncated=True)
