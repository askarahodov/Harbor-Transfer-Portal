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


@dataclass(frozen=True)
class DiscoveryResult:
    available: bool
    items: tuple[str, ...]
    message: str | None = None
    truncated: bool = False


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

    async def _search_rest(self, repository: str | None, search: str) -> DiscoveryResult:
        """Read Nexus UI Search API for one configured Docker hosted repository.

        REST search endpoints are not the Docker connector. This opt-in admin
        configuration keeps credentials off arbitrary user-supplied URLs.
        """
        if not self.search_url or not self.search_repository:
            return DiscoveryResult(False, (), "Nexus REST Search API не настроен")
        auth = (
            httpx.BasicAuth(self.username, self.password or "")
            if self.username else None
        )
        found: set[str] = set()
        token: str | None = None
        truncated = False
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self.timeout,
                follow_redirects=False, trust_env=False,
            ) as client:
                for page in range(5):
                    params = {"repository": self.search_repository, "format": "docker"}
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
                        if not isinstance(name, str) or _REPOSITORY.fullmatch(name) is None:
                            continue
                        if repository is None:
                            if search.casefold() in name.casefold():
                                found.add(name)
                        elif name == repository and isinstance(version, str):
                            if _TAG.fullmatch(version) and search.casefold() in version.casefold():
                                found.add(version)
                    next_token = data.get("continuationToken")
                    if not isinstance(next_token, str) or not next_token:
                        break
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
        # A bounded read-only query. If Nexus does not expose a catalog or the
        # account lacks list privileges, do not pretend the repository is empty.
        auth = (
            httpx.BasicAuth(self.username, self.password or "")
            if self.username else None
        )
        try:
            async with httpx.AsyncClient(
                verify=self.verify,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(
                    self.url + path, params={"n": 100}, auth=auth
                )
        except (httpx.TransportError, httpx.InvalidURL, ValueError):
            return DiscoveryResult(
                False, (), "Каталог Nexus недоступен; используйте точный repository:tag"
            )
        if response.status_code != 200:
            return DiscoveryResult(
                False, (),
                "Nexus не разрешает перечисление образов/тегов; точный repository:tag доступен",
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
        filtered = tuple(sorted({
            value for value in values
            if pattern.fullmatch(value) is not None
            and search.casefold() in value.casefold()
        }))
        # Nexus may paginate the catalog with a Link header; this first-page
        # result is suggestions, not an authoritative complete inventory.
        return DiscoveryResult(
            True, filtered[:100],
            truncated="link" in response.headers or len(values) >= 100,
        )
