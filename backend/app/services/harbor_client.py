from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.config import Settings

logger = logging.getLogger(__name__)


class HarborProject(BaseModel):
    project_id: int
    name: str
    public: bool = False


class HarborRepository(BaseModel):
    id: int | None = None
    name: str
    artifact_count: int | None = None
    pull_count: int | None = None


class HarborTag(BaseModel):
    name: str
    push_time: datetime | None = None


class HarborArtifact(BaseModel):
    digest: str
    type: str | None = None
    media_type: str | None = None
    size: int | None = None
    push_time: datetime | None = None
    tags: list[HarborTag] = Field(default_factory=list)
    extra_attrs: dict[str, Any] = Field(default_factory=dict)


class HarborSystemInfo(BaseModel):
    harbor_version: str | None = None
    auth_mode: str | None = None


@dataclass(slots=True)
class HarborClientError(Exception):
    code: str
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


class HarborClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        verify: bool | str = True,
        connect_timeout: float = 5.0,
        read_timeout: float = 20.0,
        page_size: int = 100,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if page_size < 1 or page_size > 100:
            raise ValueError("page_size must be between 1 and 100")
        self.base_url = base_url.rstrip("/")
        self.verify = verify
        self.page_size = page_size
        auth = httpx.BasicAuth(username, password or "") if username else None
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=auth,
            verify=verify,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            headers={"Accept": "application/json"},
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> HarborClient:
        if settings.harbor_url is None:
            raise ValueError("HARBOR_URL is required")
        verify: bool | str
        if settings.harbor_ca_file is not None:
            verify = str(settings.harbor_ca_file)
        else:
            verify = settings.harbor_verify_tls
        if verify is False:
            logger.warning("Harbor TLS certificate verification is explicitly disabled")
        password = settings.harbor_password.get_secret_value() if settings.harbor_password else None
        return cls(
            base_url=str(settings.harbor_url),
            username=settings.harbor_user,
            password=password,
            verify=verify,
            connect_timeout=settings.harbor_connect_timeout_seconds,
            read_timeout=settings.harbor_read_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HarborClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def system_info(self) -> HarborSystemInfo:
        response = self._request("GET", "/api/v2.0/systeminfo")
        return HarborSystemInfo.model_validate(self._json_object(response))

    def list_projects(self) -> list[HarborProject]:
        return [
            HarborProject.model_validate(item)
            for item in self._paginate("/api/v2.0/projects")
        ]

    def list_repositories(self, project: str) -> list[HarborRepository]:
        encoded_project = quote(project, safe="")
        path = f"/api/v2.0/projects/{encoded_project}/repositories"
        return [HarborRepository.model_validate(item) for item in self._paginate(path)]

    def list_artifacts(self, project: str, repository: str) -> list[HarborArtifact]:
        encoded_project = quote(project, safe="")
        encoded_repo = quote(repository, safe="")
        path = f"/api/v2.0/projects/{encoded_project}/repositories/{encoded_repo}/artifacts"
        return [
            HarborArtifact.model_validate(item)
            for item in self._paginate(path, params={"with_tag": "true"})
        ]

    def get_artifact(self, project: str, repository: str, reference: str) -> HarborArtifact:
        encoded_project = quote(project, safe="")
        encoded_repo = quote(repository, safe="")
        encoded_reference = quote(reference, safe="")
        path = (
            f"/api/v2.0/projects/{encoded_project}/repositories/{encoded_repo}"
            f"/artifacts/{encoded_reference}"
        )
        response = self._request("GET", path, params={"with_tag": "true"})
        return HarborArtifact.model_validate(self._json_object(response))

    def reference_digest(self, project: str, repository: str, reference: str) -> str | None:
        try:
            return self.get_artifact(project, repository, reference).digest
        except HarborClientError as exc:
            if exc.code == "not_found":
                return None
            raise

    def _paginate(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        collected: list[dict[str, Any]] = []
        while True:
            query: dict[str, str | int] = dict(params or {})
            query.update({"page": page, "page_size": self.page_size})
            response = self._request("GET", path, params=query)
            payload = self._json_list(response)
            collected.extend(payload)
            total_header = response.headers.get("X-Total-Count")
            total = int(total_header) if total_header and total_header.isdigit() else None
            if (total is not None and len(collected) >= total) or len(payload) < self.page_size:
                return collected
            page += 1

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise HarborClientError("timeout", "Local Harbor request timed out") from exc
        except httpx.HTTPError as exc:
            raise HarborClientError(
                "connection_failed",
                "Unable to connect to local Harbor",
            ) from exc

        if response.is_success:
            return response
        mapping = {
            401: ("unauthorized", "Harbor authentication failed"),
            403: ("forbidden", "Harbor denied access"),
            404: ("not_found", "Harbor resource was not found"),
            429: ("rate_limited", "Harbor rate limit was reached"),
        }
        if response.status_code in mapping:
            code, message = mapping[response.status_code]
            raise HarborClientError(code, message, response.status_code)
        if response.status_code >= 500:
            raise HarborClientError(
                "harbor_unavailable",
                "Local Harbor is temporarily unavailable",
                response.status_code,
            )
        raise HarborClientError("harbor_error", "Local Harbor request failed", response.status_code)

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise HarborClientError("invalid_response", "Harbor returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HarborClientError("invalid_response", "Harbor returned an invalid object response")
        return payload

    @staticmethod
    def _json_list(response: httpx.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise HarborClientError("invalid_response", "Harbor returned invalid JSON") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise HarborClientError("invalid_response", "Harbor returned an invalid list response")
        return payload
