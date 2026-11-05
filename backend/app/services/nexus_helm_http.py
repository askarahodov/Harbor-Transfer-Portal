"""Non-OCI Nexus Helm HTTP repository transport.

Repository access is scoped to the configured service identity; Docker OCI transports
are never used for Helm. All outgoing URLs are derived from a trusted Nexus UI origin.
"""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import yaml
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.nexus_discovery import NexusDiscoveryService

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CHART = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_INDEX = 4 * 1024 * 1024
_MAX_CHART = 100 * 1024 * 1024


@dataclass(frozen=True)
class HelmChartVersion:
    name: str
    version: str
    path: str
    digest: str


@dataclass(frozen=True)
class HelmHttpResult:
    available: bool
    items: tuple[HelmChartVersion, ...] = ()
    code: str | None = None
    message: str | None = None


class NexusHelmHttpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class NexusHelmHttpService:
    """Bounded, read/write Helm hosted protocol with no OCI or URL redirects."""

    def __init__(self, session: Session, settings: Settings, profile_id: str) -> None:
        discovery = NexusDiscoveryService(session, settings, profile_id)
        self.base_url = discovery.search_url or ""
        self.username = discovery.username
        self.password = discovery.password
        self.verify = discovery.verify
        self.timeout = discovery.timeout

    def _base(self) -> str:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise NexusHelmHttpError("helm_url_invalid", "Требуется корневой Nexus UI HTTPS URL")
        return self.base_url.rstrip("/")

    @staticmethod
    def _repository(repository: str) -> str:
        if _REPO.fullmatch(repository) is None:
            raise NexusHelmHttpError("helm_repository_invalid", "Некорректное имя Helm repository")
        return repository

    def _client(self) -> httpx.AsyncClient:
        auth = (
            httpx.BasicAuth(self.username, self.password or "")
            if self.username
            else None
        )
        return httpx.AsyncClient(
            auth=auth, verify=self.verify, timeout=self.timeout,
            follow_redirects=False, trust_env=False,
        )

    async def _load_index(self, client: httpx.AsyncClient, repository: str) -> HelmHttpResult:
        url = self._base() + "/repository/" + quote(self._repository(repository)) + "/index.yaml"
        try:
            async with client.stream("GET", url) as response:
                if response.status_code in (401, 403, 404):
                    return HelmHttpResult(False, code="helm_repository_unavailable",
                                          message="Helm repository недоступен для чтения")
                if response.status_code != 200:
                    return HelmHttpResult(False, code="helm_index_unavailable",
                                          message="Не удалось прочитать Helm index.yaml")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_INDEX:
                        return HelmHttpResult(False, code="helm_index_too_large",
                                              message="Helm index.yaml превышает лимит")
        except httpx.HTTPError:
            return HelmHttpResult(False, code="helm_index_unavailable",
                                  message="Helm repository недоступен")
        try:
            data = yaml.safe_load(bytes(content))
        except (yaml.YAMLError, UnicodeError):
            return HelmHttpResult(False, code="helm_index_invalid",
                                  message="Неверный формат Helm index.yaml")
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return HelmHttpResult(False, code="helm_index_invalid",
                                  message="Некорректный Helm index.yaml")
        found: list[HelmChartVersion] = []
        for name, versions in data["entries"].items():
            if not isinstance(name, str) or _CHART.fullmatch(name) is None:
                continue
            if not isinstance(versions, list):
                continue
            if len(versions) > 500:
                return HelmHttpResult(
                    False, code="helm_index_too_large",
                    message="Helm index.yaml содержит слишком много версий",
                )
            for item in versions:
                if not isinstance(item, dict):
                    continue
                version, digest, urls = item.get("version"), item.get("digest"), item.get("urls")
                if (
                    not isinstance(version, str) or _VERSION.fullmatch(version) is None
                    or not isinstance(digest, str) or _SHA256.fullmatch(digest.lower()) is None
                    or not isinstance(urls, list) or not urls
                    or not isinstance(urls[0], str)
                ):
                    continue
                path = urls[0]
                # No cross-origin URL or traversal: only Nexus-root chart filenames.
                if (
                    path != f"{name}-{version}.tgz"
                    or "/" in path or "\\" in path or "?" in path or "#" in path
                ):
                    continue
                found.append(HelmChartVersion(name, version, path, digest.lower()))
                if len(found) > 500:
                    return HelmHttpResult(
                        False, code="helm_index_too_large",
                        message="Helm index.yaml содержит слишком много charts",
                    )
        return HelmHttpResult(True, tuple(found))

    async def list_charts(self, repository: str) -> HelmHttpResult:
        self._base()
        self._repository(repository)
        async with self._client() as client:
            return await self._load_index(client, repository)

    async def download_chart(
        self, repository: str, name: str, version: str, destination: Path
    ) -> HelmChartVersion:
        if _CHART.fullmatch(name) is None or _VERSION.fullmatch(version) is None:
            raise NexusHelmHttpError("helm_reference_invalid", "Некорректный Helm chart/version")
        if destination.exists() or destination.is_symlink():
            raise NexusHelmHttpError("helm_destination_exists", "Файл назначения уже существует")
        async with self._client() as client:
            inventory = await self._load_index(client, repository)
            if not inventory.available:
                raise NexusHelmHttpError(inventory.code or "helm_index_unavailable",
                                         inventory.message or "Helm index недоступен")
            entry = next(
                (v for v in inventory.items if v.name == name and v.version == version), None
            )
            if entry is None:
                raise NexusHelmHttpError("helm_chart_not_found", "Helm chart/version не найден")
            url = self._base() + "/repository/" + quote(repository) + "/" + quote(entry.path)
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise NexusHelmHttpError(
                            "helm_chart_unavailable", "Helm package недоступен"
                        )
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > _MAX_CHART:
                            raise NexusHelmHttpError(
                                "helm_chart_too_large", "Helm package превышает лимит"
                            )
            except httpx.HTTPError as exc:
                raise NexusHelmHttpError(
                    "helm_chart_unavailable", "Helm package недоступен"
                ) from exc
        if hashlib.sha256(chunks).hexdigest() != entry.digest:
            raise NexusHelmHttpError("helm_digest_mismatch", "Checksum Helm package не совпадает")
        self._validate_package(bytes(chunks), name, version)
        # O_EXCL prevents overwriting an existing chart. Caller owns cleanup.
        import os
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(chunks)
        return entry

    @staticmethod
    def _validate_package(data: bytes, name: str, version: str) -> None:
        """Reject malformed archives, unsafe members and mismatched Chart.yaml."""
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                members = archive.getmembers()
                if len(members) > 1000 or sum(m.size for m in members) > _MAX_CHART:
                    raise ValueError("archive limits")
                for member in members:
                    if (
                        member.issym() or member.islnk() or member.isdev()
                        or member.name.startswith("/")
                        or any(p in ("", ".", "..") for p in member.name.split("/"))
                        or not (member.isfile() or member.isdir())
                    ):
                        raise ValueError("unsafe archive member")
                metadata = next(
                    (m for m in members if m.name == f"{name}/Chart.yaml" and m.isfile()),
                    None,
                )
                if metadata is None or metadata.size > 64 * 1024:
                    raise ValueError("Chart.yaml missing")
                stream = archive.extractfile(metadata)
                if stream is None:
                    raise ValueError("Chart.yaml unreadable")
                manifest = yaml.safe_load(stream.read())
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("name") != name
                    or manifest.get("version") != version
                ):
                    raise ValueError("Chart.yaml identity mismatch")
        except (tarfile.TarError, yaml.YAMLError, OSError, ValueError) as exc:
            raise NexusHelmHttpError(
                "helm_package_invalid", "Некорректный или небезопасный Helm package"
            ) from exc

    async def upload_chart(
        self, repository: str, name: str, version: str, package: Path
    ) -> str:
        """Upload to Helm hosted via Components API. Never overwrite an existing version."""
        self._base()
        self._repository(repository)
        if _CHART.fullmatch(name) is None or _VERSION.fullmatch(version) is None:
            raise NexusHelmHttpError("helm_reference_invalid", "Некорректный Helm chart/version")
        if package.is_symlink() or not package.is_file() or package.suffix != ".tgz":
            raise NexusHelmHttpError("helm_package_invalid", "Ожидается обычный Helm .tgz файл")
        if package.stat().st_size > _MAX_CHART:
            raise NexusHelmHttpError("helm_chart_too_large", "Helm package превышает лимит")
        data = package.read_bytes()
        self._validate_package(data, name, version)
        digest = hashlib.sha256(data).hexdigest()
        async with self._client() as client:
            # No writes to proxy/group or repositories whose type cannot be verified.
            meta_url = self._base() + "/service/rest/v1/repositories/" + quote(repository)
            try:
                meta = await client.get(meta_url)
            except httpx.HTTPError as exc:
                raise NexusHelmHttpError(
                    "helm_repository_unavailable", "Нет доступа к Helm repository"
                ) from exc
            if meta.status_code != 200:
                raise NexusHelmHttpError(
                    "helm_repository_unavailable", "Нет доступа к Helm repository"
                )
            try:
                details = meta.json()
            except ValueError as exc:
                raise NexusHelmHttpError(
                    "helm_repository_invalid", "Некорректный ответ Nexus"
                ) from exc
            if (
                not isinstance(details, dict)
                or details.get("format") != "helm"
                or details.get("type") != "hosted"
            ):
                raise NexusHelmHttpError(
                    "helm_repository_read_only", "Запись разрешена только в Helm hosted"
                )
            current = await self._load_index(client, repository)
            if not current.available:
                raise NexusHelmHttpError(current.code or "helm_index_unavailable",
                                         current.message or "Нет доступа к index.yaml")
            if any(v.name == name and v.version == version for v in current.items):
                raise NexusHelmHttpError("helm_target_exists", "Helm chart/version уже существует")
            try:
                response = await client.post(
                    self._base() + "/service/rest/v1/components",
                    params={"repository": repository},
                    files={"helm.asset": (f"{name}-{version}.tgz", data, "application/gzip")},
                )
            except httpx.HTTPError as exc:
                raise NexusHelmHttpError("helm_upload_failed", "Helm upload недоступен") from exc
            if response.status_code not in (200, 201, 204):
                raise NexusHelmHttpError("helm_upload_failed", "Helm repository отклонил upload")
            observed = await self._load_index(client, repository)
            if not observed.available:
                raise NexusHelmHttpError("helm_postcheck_failed", "Helm upload не подтверждён")
            match = next(
                (v for v in observed.items if v.name == name and v.version == version), None
            )
            if match is None or match.digest != digest:
                raise NexusHelmHttpError(
                    "helm_postcheck_failed", "Helm upload checksum не подтверждён"
                )
        return digest
