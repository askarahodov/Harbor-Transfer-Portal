"""Non-OCI Nexus Helm HTTP repository transport.

Repository access is scoped to the configured service identity; Docker OCI transports
are never used for Helm. All outgoing URLs are derived from a trusted Nexus UI origin.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import yaml
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmPackageMetadata,
    HelmPullResult,
    HelmPushResult,
    HelmServiceError,
    HelmTargetInspection,
    HelmTargetState,
)
from app.services.harbor_profiles import HarborProfileService
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService
from app.services.nexus_discovery import NexusDiscoveryService

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CHART = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_INDEX = 4 * 1024 * 1024
_MAX_CHART = 100 * 1024 * 1024


class _UniqueKeysLoader(yaml.SafeLoader):
    """Fail closed on duplicate YAML mapping keys, including nested metadata."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while reading YAML mapping", node.start_mark,
                    "unhashable mapping key", key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while reading YAML mapping", node.start_mark,
                    "duplicate mapping key", key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _load_unambiguous_yaml(data: bytes) -> object:
    loader = _UniqueKeysLoader(data)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


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
        profile = HarborProfileService(session, settings).operation_snapshot(profile_id)
        if profile.registry_type != "nexus":
            raise HarborSettingsError("registry_type_mismatch", "Требуется Nexus Helm HTTP profile")
        self.allowed_repository: str | None = None
        if profile.nexus_format == "helm":
            # A dedicated Helm profile uses the Nexus UI origin itself, not the
            # Docker connector. Keep exact repository identity bound to its profile.
            effective = HarborSettingsService(session, settings).resolve_profile(profile.id)
            self.base_url = effective.url or ""
            self.allowed_repository = profile.nexus_search_repository
            self.username = effective.username
            self.password = effective.password
            self.verify = (
                str(effective.ca_file)
                if effective.verify_tls and effective.ca_file is not None
                else effective.verify_tls
            )
            self.timeout = httpx.Timeout(
                settings.nexus_read_timeout_seconds,
                connect=settings.nexus_connect_timeout_seconds,
            )
        else:
            # Preserve existing Docker profiles that also browse Helm via their
            # explicitly configured Nexus UI search origin.
            discovery = NexusDiscoveryService(session, settings, profile_id)
            self.base_url = discovery.search_url or ""
            self.username = discovery.username
            self.password = discovery.password
            self.verify = discovery.verify
            self.timeout = discovery.timeout

    def _base(self) -> str:
        try:
            parsed = urlsplit(self.base_url)
            hostname, port = parsed.hostname, parsed.port
            valid = (
                parsed.scheme == "https"
                and hostname is not None
                and port != 0
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
                and parsed.username is None
                and parsed.password is None
            )
        except ValueError:
            valid = False
        if not valid:
            raise NexusHelmHttpError("helm_url_invalid", "Требуется корневой Nexus UI HTTPS URL")
        return self.base_url.rstrip("/")

    def _repository(self, repository: str) -> str:
        if _REPO.fullmatch(repository) is None:
            raise NexusHelmHttpError("helm_repository_invalid", "Некорректное имя Helm repository")
        if (
            getattr(self, "allowed_repository", None) is not None
            and repository != self.allowed_repository
        ):
            raise NexusHelmHttpError(
                "helm_repository_mismatch", "Helm repository не совпадает с выбранным профилем"
            )
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

    async def _require_hosted_repository(
        self, client: httpx.AsyncClient, repository: str
    ) -> None:
        """Confirm exact TARGET identity before planning or uploading a Helm chart."""
        url = (
            self._base() + "/service/rest/v1/repositories/"
            + quote(self._repository(repository))
        )
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise NexusHelmHttpError(
                "helm_repository_unavailable", "Нет доступа к Helm repository"
            ) from exc
        if response.status_code != 200:
            raise NexusHelmHttpError(
                "helm_repository_unavailable", "Нет доступа к Helm repository"
            )
        try:
            details = response.json()
        except ValueError as exc:
            raise NexusHelmHttpError(
                "helm_repository_invalid", "Некорректный ответ Nexus"
            ) from exc
        if (
            not isinstance(details, dict)
            or details.get("name") != repository
            or details.get("format") != "helm"
            or details.get("type") != "hosted"
        ):
            raise NexusHelmHttpError(
                "helm_repository_read_only",
                "Запись разрешена только в подтверждённый Helm hosted repository",
            )

    async def _load_index(
        self, client: httpx.AsyncClient, repository: str, *, strict: bool = False
    ) -> HelmHttpResult:
        url = self._base() + "/repository/" + quote(self._repository(repository)) + "/index.yaml"
        try:
            async with client.stream("GET", url) as response:
                if response.status_code in (401, 403):
                    return HelmHttpResult(
                        False, code="helm_repository_denied",
                        message="Нет прав на чтение Helm repository",
                    )
                if response.status_code == 404:
                    return HelmHttpResult(
                        False, code="helm_index_missing",
                        message="Helm index.yaml отсутствует",
                    )
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
            data = _load_unambiguous_yaml(bytes(content))
        except (yaml.YAMLError, UnicodeError):
            return HelmHttpResult(False, code="helm_index_invalid",
                                  message="Неверный формат Helm index.yaml")
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return HelmHttpResult(False, code="helm_index_invalid",
                                  message="Некорректный Helm index.yaml")
        found: list[HelmChartVersion] = []
        seen_versions: set[tuple[str, str]] = set()
        for name, versions in data["entries"].items():
            if not isinstance(name, str) or _CHART.fullmatch(name) is None:
                if strict:
                    return HelmHttpResult(
                        False, code="helm_index_invalid",
                        message="Helm index содержит некорректное имя chart",
                    )
                continue
            if not isinstance(versions, list):
                if strict:
                    return HelmHttpResult(
                        False, code="helm_index_invalid",
                        message="Helm index содержит некорректный список версий",
                    )
                continue
            if len(versions) > 500:
                return HelmHttpResult(
                    False, code="helm_index_too_large",
                    message="Helm index.yaml содержит слишком много версий",
                )
            for item in versions:
                if not isinstance(item, dict):
                    if strict:
                        return HelmHttpResult(
                            False, code="helm_index_invalid",
                            message="Helm index содержит некорректную запись версии",
                        )
                    continue
                version, digest, urls = item.get("version"), item.get("digest"), item.get("urls")
                if (
                    not isinstance(version, str) or _VERSION.fullmatch(version) is None
                    or not isinstance(digest, str) or _SHA256.fullmatch(digest.lower()) is None
                    or not isinstance(urls, list) or not urls
                    or not isinstance(urls[0], str)
                ):
                    if strict:
                        return HelmHttpResult(
                            False, code="helm_index_invalid",
                            message="Helm index содержит непроверяемую версию",
                        )
                    continue
                filename = f"{name}-{version}.tgz"
                # Never follow advertised URLs. Inspect every mirror URL,
                # not only the first; hidden unsafe alternatives must not
                # turn a malformed catalog into a valid signed export.
                allowed_paths = {
                    filename,
                    f"/{filename}",
                    f"/repository/{repository}/{filename}",
                }
                unsafe = False
                for advertised in urls:
                    if not isinstance(advertised, str):
                        unsafe = True
                        break
                    try:
                        parsed_url = urlsplit(advertised)
                    except ValueError:
                        unsafe = True
                        break
                    if (
                        parsed_url.path not in allowed_paths
                        or parsed_url.query or parsed_url.fragment
                        or parsed_url.username or parsed_url.password
                        or "\\" in advertised
                        or (parsed_url.scheme and parsed_url.scheme not in {"http", "https"})
                        or (parsed_url.netloc and parsed_url.path != (
                            f"/repository/{repository}/{filename}"
                        ))
                    ):
                        unsafe = True
                        break
                if unsafe:
                    if strict:
                        return HelmHttpResult(
                            False, code="helm_index_invalid",
                            message="Helm index содержит небезопасный URL пакета",
                        )
                    continue
                identity = (name, version)
                if strict and identity in seen_versions:
                    return HelmHttpResult(
                        False, code="helm_index_invalid",
                        message="Helm index содержит повторяющуюся chart/version",
                    )
                seen_versions.add(identity)
                found.append(HelmChartVersion(name, version, filename, digest.lower()))
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
            # SOURCE must never sign an ambiguously indexed chart. The
            # exploratory catalog can filter malformed entries, but bundle
            # materialization requires the same fail-closed rules as TARGET.
            inventory = await self._load_index(client, repository, strict=True)
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
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(chunks)
        return entry

    @staticmethod
    def _validate_package(data: bytes, name: str, version: str) -> None:
        """Reject malformed archives, unsafe members and mismatched Chart.yaml."""
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                # Parse one header at a time: getmembers() would first cache
                # the entire untrusted tar directory before applying limits.
                seen_members: set[str] = set()
                total_size = 0
                chart_metadata_seen = False
                for member in archive:
                    if len(seen_members) >= 1000:
                        raise ValueError("archive member limit")
                    total_size += member.size
                    if total_size > _MAX_CHART:
                        raise ValueError("archive size limit")
                    normalized_name = member.name.rstrip("/")
                    if normalized_name in seen_members:
                        raise ValueError("duplicate archive member")
                    seen_members.add(normalized_name)
                    if (
                        member.issym() or member.islnk() or member.isdev()
                        or member.name.startswith("/")
                        or not normalized_name
                        or any(
                            p in ("", ".", "..")
                            for p in normalized_name.split("/")
                        )
                        or not (member.isfile() or member.isdir())
                    ):
                        raise ValueError("unsafe archive member")
                    if member.name == f"{name}/Chart.yaml":
                        if not member.isfile() or member.size > 64 * 1024:
                            raise ValueError("Chart.yaml invalid")
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise ValueError("Chart.yaml unreadable")
                        manifest = _load_unambiguous_yaml(stream.read(64 * 1024 + 1))
                        if (
                            not isinstance(manifest, dict)
                            or manifest.get("name") != name
                            or manifest.get("version") != version
                        ):
                            raise ValueError("Chart.yaml identity mismatch")
                        chart_metadata_seen = True
                if not chart_metadata_seen:
                    raise ValueError("Chart.yaml missing")
        except (tarfile.TarError, yaml.YAMLError, OSError, ValueError) as exc:
            raise NexusHelmHttpError(
                "helm_package_invalid", "Некорректный или небезопасный Helm package"
            ) from exc

    @staticmethod
    def _read_package_bytes(package: Path) -> bytes:
        """Read a bounded snapshot without following a swapped-in symlink."""
        if package.suffix != ".tgz":
            raise NexusHelmHttpError("helm_package_invalid", "Ожидается обычный Helm .tgz файл")
        try:
            fd = os.open(package, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise NexusHelmHttpError(
                        "helm_package_invalid", "Ожидается обычный Helm .tgz файл"
                    )
                if info.st_size > _MAX_CHART:
                    raise NexusHelmHttpError(
                        "helm_chart_too_large", "Helm package превышает лимит"
                    )
                data = handle.read(_MAX_CHART + 1)
                if len(data) > _MAX_CHART:
                    raise NexusHelmHttpError(
                        "helm_chart_too_large", "Helm package превышает лимит"
                    )
                return data
        except OSError as exc:
            raise NexusHelmHttpError(
                "helm_package_invalid", "Helm package недоступен или небезопасен"
            ) from exc

    async def upload_chart(
        self, repository: str, name: str, version: str, package: Path,
        *, expected_digest: str | None = None,
    ) -> str:
        """Upload to Helm hosted via Components API. Never overwrite an existing version."""
        self._base()
        self._repository(repository)
        if _CHART.fullmatch(name) is None or _VERSION.fullmatch(version) is None:
            raise NexusHelmHttpError("helm_reference_invalid", "Некорректный Helm chart/version")
        data = self._read_package_bytes(package)
        digest = hashlib.sha256(data).hexdigest()
        # Check the signed SOURCE digest against the exact byte snapshot
        # supplied in the upload body, not an earlier read of the path.
        if expected_digest is not None and expected_digest != "sha256:" + digest:
            raise NexusHelmHttpError(
                "helm_payload_digest_mismatch",
                "Helm package не совпадает с подписанным SOURCE digest",
            )
        self._validate_package(data, name, version)
        async with self._client() as client:
            # TARGET planning and write must agree on the exact hosted identity.
            await self._require_hosted_repository(client, repository)
            # A malformed entry must never be interpreted as an absent version
            # during pre-write conflict detection.
            current = await self._load_index(client, repository, strict=True)
            if not current.available and current.code != "helm_index_missing":
                raise NexusHelmHttpError(
                    current.code or "helm_index_unavailable",
                    current.message or "Нет доступа к index.yaml",
                )
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
            # Nexus updates Helm index.yaml asynchronously after a successful
            # Components upload. Never resend the POST: only retry read-only
            # postchecks and fail closed if the version is not published.
            for attempt in range(20):
                observed = await self._load_index(client, repository, strict=True)
                match = next(
                    (v for v in observed.items if v.name == name and v.version == version),
                    None,
                ) if observed.available else None
                if match is not None:
                    if match.digest != digest:
                        raise NexusHelmHttpError(
                            "helm_postcheck_failed",
                            "Helm index содержит другой checksum после upload",
                        )
                    break
                if not observed.available and observed.code not in {"helm_index_missing"}:
                    raise NexusHelmHttpError(
                        "helm_postcheck_failed", "Helm index недоступен после upload"
                    )
                if attempt < 19:
                    await asyncio.sleep(0.5)
            else:
                raise NexusHelmHttpError(
                    "helm_postcheck_failed", "Helm chart/version отсутствует в index после upload"
                )
        return digest


class NexusHelmAdapter:
    """Translate classic Helm HTTP semantics to the bundle pipeline's chart interface."""

    def __init__(self, session: Session, settings: Settings, profile_id: str) -> None:
        self.client = NexusHelmHttpService(session, settings, profile_id)

    @staticmethod
    def _error(exc: NexusHelmHttpError) -> HelmServiceError:
        return HelmServiceError(exc.code, exc.message)

    async def inspect_target(
        self, chart: HelmChartReference, *, expected_digest: str | None = None
    ) -> HelmTargetInspection:
        try:
            async with self.client._client() as http:
                result = await self.client._load_index(http, chart.repository, strict=True)
                # Reading an index proves neither hosted type nor repository identity.
                # TARGET must refuse NEW/SAME/CONFLICT planning for proxy/group or
                # foreign repositories, even when the chart is already indexed.
                if result.available or result.code == "helm_index_missing":
                    await self.client._require_hosted_repository(http, chart.repository)
        except NexusHelmHttpError as exc:
            raise self._error(exc) from exc
        if not result.available and result.code == "helm_index_missing":
            # A new hosted repository may have no index before its first upload.
            return HelmTargetInspection(HelmTargetState.ABSENT, None)
        if not result.available:
            raise HelmServiceError(
                result.code or "helm_index_unavailable",
                result.message or "Helm index.yaml недоступен",
            )
        match = next(
            (v for v in result.items if v.name == chart.name and v.version == chart.version),
            None,
        )
        if match is None:
            return HelmTargetInspection(HelmTargetState.ABSENT, None)
        digest = "sha256:" + match.digest
        state = (
            HelmTargetState.SAME_DIGEST
            if expected_digest == digest
            else HelmTargetState.CONFLICTING_DIGEST
            if expected_digest is not None
            else HelmTargetState.PRESENT
        )
        return HelmTargetInspection(state, digest)

    async def pull_chart(
        self, chart: HelmChartReference, destination: Path
    ) -> HelmPullResult:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        output = destination / f"{chart.name}-{chart.version}.tgz"
        try:
            item = await self.client.download_chart(
                chart.repository, chart.name, chart.version, output
            )
        except NexusHelmHttpError as exc:
            raise self._error(exc) from exc
        package = HelmPackageMetadata(output, chart.name, chart.version, item.digest)
        return HelmPullResult("sha256:" + item.digest, package)

    async def push_chart(
        self, package: Path, target: HelmChartReference, *,
        source_digest: str | None = None, allow_existing: bool = False,
    ) -> HelmPushResult:
        if allow_existing:
            raise HelmServiceError(
                "helm_overwrite_unsupported",
                "Nexus Helm HTTP overwrite запрещён: требуется отдельная approval-политика",
            )
        try:
            digest = await self.client.upload_chart(
                target.repository, target.name, target.version, package,
                expected_digest=source_digest,
            )
        except NexusHelmHttpError as exc:
            raise self._error(exc) from exc
        target_digest = "sha256:" + digest
        if source_digest is not None and source_digest != target_digest:
            raise HelmServiceError(
                "helm_payload_digest_mismatch",
                "Helm HTTP payload не совпадает с подписанным SOURCE digest",
            )
        return HelmPushResult(
            package=HelmPackageMetadata(package, target.name, target.version, digest),
            target_digest=target_digest,
            source_digest=source_digest,
            digest_matches_source=(
                source_digest == target_digest if source_digest is not None else None
            ),
        )
