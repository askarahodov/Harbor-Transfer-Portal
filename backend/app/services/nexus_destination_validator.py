"""Read-only Docker v2 preflight for a Nexus TARGET destination.

Nexus Docker repositories are not Harbor projects. Connectivity and authenticated
registry access can be checked without Harbor REST API or test pushes. A successful
probe is *not* proof of push rights; the import adapter remains authoritative and
the operator must verify write permissions on their target namespace.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_destination_validator import DestinationCapability
from app.services.harbor_profiles import HarborProfileService
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService


class NexusDestinationValidator:
    """Validate a bound Nexus Docker v2 connector without mutating the registry."""

    def __init__(
        self, session: Session, settings: Settings, *, profile_id: str
    ) -> None:
        self.settings = settings
        profile = HarborProfileService(session, settings).operation_snapshot(profile_id)
        if profile.registry_type != "nexus":
            raise HarborSettingsError(
                "registry_type_mismatch", "Требуется профиль Nexus Docker v2"
            )
        resolved = HarborSettingsService(session, settings).resolve_profile(profile.id)
        if not resolved.url:
            raise HarborSettingsError(
                "nexus_not_configured", "Docker Registry v2 URL не настроен"
            )
        parsed = urlsplit(resolved.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise HarborSettingsError(
                "nexus_url_invalid", "Ожидается корневой URL Docker Registry v2"
            )
        self._url = resolved.url.rstrip("/")
        self._registry_host = parsed.netloc
        self._username = resolved.username
        self._password = resolved.password
        self._verify: bool | str = (
            str(resolved.ca_file)
            if resolved.verify_tls and resolved.ca_file is not None
            else resolved.verify_tls
        )

    @property
    def registry_host(self) -> str:
        return self._registry_host

    async def validate(self, project: str, repository: str) -> DestinationCapability:
        # Only one read-only connector probe: Nexus has no Harbor project API.
        # A successful GET does not claim that registry push is permitted.
        auth = (
            httpx.BasicAuth(self._username, self._password or "")
            if self._username
            else None
        )
        try:
            async with httpx.AsyncClient(
                base_url=self._url,
                auth=auth,
                verify=self._verify,
                follow_redirects=False,
                timeout=httpx.Timeout(
                    self.settings.harbor_read_timeout_seconds,
                    connect=self.settings.harbor_connect_timeout_seconds,
                ),
            ) as client:
                response = await client.get("/v2/")
        except httpx.TimeoutException:
            return DestinationCapability(False, False, "nexus_timeout", "Nexus не ответил вовремя")
        except httpx.TransportError:
            return DestinationCapability(
                False, False, "nexus_registry_connection_failed",
                "Не удалось подключиться к TARGET Nexus Docker v2",
            )
        if response.status_code in {401, 403}:
            return DestinationCapability(
                False, False, "nexus_auth_failed",
                "Nexus отклонил учётные данные для TARGET",
            )
        if response.status_code != 200:
            return DestinationCapability(
                False, False, "nexus_registry_connection_failed",
                "TARGET endpoint не подтвердил Docker Registry v2 доступ",
            )
        if response.headers.get("Docker-Distribution-API-Version", "").lower() != "registry/2.0":
            return DestinationCapability(
                False, False, "nexus_url_invalid",
                "TARGET endpoint не является Docker Registry v2 connector",
            )
        # Eligibility to plan import, not an assertion of confirmed push RBAC.
        # Skopeo import_image performs the authorized write and post-inspection.
        return DestinationCapability(True, True)
