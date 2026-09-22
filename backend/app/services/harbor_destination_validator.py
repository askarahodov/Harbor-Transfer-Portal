from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_client import HarborClientError
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService


@dataclass(frozen=True, slots=True)
class DestinationCapability:
    project_exists: bool
    write_allowed: bool
    error_code: str | None = None
    message: str | None = None


class DestinationValidator(Protocol):
    @property
    def registry_host(self) -> str: ...

    async def validate(self, project: str, repository: str) -> DestinationCapability: ...


class HarborDestinationValidator:
    """Validate TARGET project existence and repository push capability without mutation."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        profile_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.harbor_settings = HarborSettingsService(session, settings)
        self.profile_id = profile_id
        resolved = (
            self.harbor_settings.resolve()
            if profile_id is None
            else self.harbor_settings.resolve_profile(profile_id)
        )
        if not resolved.url:
            raise HarborSettingsError("harbor_not_configured", "Локальный Harbor не настроен")
        self._resolved = resolved
        self._base_url = resolved.url
        self._registry_host = urlsplit(self._base_url).netloc
        if not self._registry_host:
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "URL локального Harbor не содержит registry host",
            )
        self._project_cache: dict[str, bool] = {}
        self._write_cache: dict[str, bool] = {}

    @property
    def registry_host(self) -> str:
        return self._registry_host

    async def validate(self, project: str, repository: str) -> DestinationCapability:
        try:
            exists = await asyncio.to_thread(self._project_exists, project)
            if not exists:
                return DestinationCapability(
                    False,
                    False,
                    "import_destination_project_missing",
                    f"TARGET Harbor project '{project}' не существует",
                )
            write_allowed = await asyncio.to_thread(self._repository_push_allowed, repository)
            if not write_allowed:
                return DestinationCapability(
                    True,
                    False,
                    "import_destination_write_forbidden",
                    f"Настроенные Harbor credentials не имеют push-доступа к '{repository}'",
                )
            return DestinationCapability(True, True)
        except (HarborClientError, HarborSettingsError, httpx.HTTPError, ValueError) as exc:
            return DestinationCapability(
                False,
                False,
                getattr(exc, "code", "import_destination_validation_failed"),
                str(exc) or "Не удалось проверить TARGET destination",
            )

    def _project_exists(self, project: str) -> bool:
        cached = self._project_cache.get(project)
        if cached is not None:
            return cached
        with (
            self.harbor_settings.build_client()
            if self.profile_id is None
            else self.harbor_settings.build_client_for_profile(self.profile_id)
        ) as client:
            page = client.list_projects_page(1, 100, search_needle=project)
        exists = any(item.name == project for item in page.items)
        self._project_cache[project] = exists
        return exists

    def _repository_push_allowed(self, repository: str) -> bool:
        cached = self._write_cache.get(repository)
        if cached is not None:
            return cached
        verify: bool | str = self._resolved.verify_tls
        if self._resolved.verify_tls and self._resolved.ca_file is not None:
            verify = str(self._resolved.ca_file)
        auth = (
            httpx.BasicAuth(self._resolved.username, self._resolved.password or "")
            if self._resolved.username
            else None
        )
        with httpx.Client(
            base_url=self._base_url,
            auth=auth,
            verify=verify,
            timeout=httpx.Timeout(
                self.settings.harbor_read_timeout_seconds,
                connect=self.settings.harbor_connect_timeout_seconds,
            ),
            headers={"Accept": "application/json"},
        ) as client:
            response = client.get(
                "/service/token",
                params={
                    "service": "harbor-registry",
                    "scope": f"repository:{repository}:pull,push",
                },
            )
        if response.status_code in {401, 403}:
            self._write_cache[repository] = False
            return False
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Harbor token endpoint returned an invalid object")
        raw_token = payload.get("token") or payload.get("access_token")
        if not isinstance(raw_token, str) or not raw_token:
            raise ValueError("Harbor token endpoint did not return a registry token")
        allowed = self._jwt_allows_push(raw_token, repository)
        self._write_cache[repository] = allowed
        return allowed

    @staticmethod
    def _jwt_allows_push(token: str, repository: str) -> bool:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Harbor registry token is not a JWT")
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Harbor registry token payload is invalid") from exc
        access = payload.get("access") if isinstance(payload, dict) else None
        if not isinstance(access, list):
            return False
        for item in access:
            if not isinstance(item, dict):
                continue
            actions = item.get("actions")
            if (
                item.get("type") == "repository"
                and item.get("name") == repository
                and isinstance(actions, list)
                and "push" in actions
            ):
                return True
        return False
