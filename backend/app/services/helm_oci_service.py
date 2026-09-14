from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from re import fullmatch
from typing import Protocol
from urllib.parse import urlsplit

import yaml
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings, HarborSettingsService

_REPOSITORY_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
_CHART_NAME_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_VERSION_PATTERN = r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}"
_DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_SAFE_LOCALE_ENV = ("LANG", "LC_ALL", "LC_CTYPE")


class HelmPhase(StrEnum):
    INSPECTING_SOURCE = "inspecting_source"
    AUTHENTICATING = "authenticating"
    PULLING = "pulling"
    VALIDATING = "validating"
    PULLED = "pulled"
    INSPECTING_TARGET = "inspecting_target"
    PUSHING = "pushing"
    VERIFYING_TARGET = "verifying_target"
    PUSHED = "pushed"


class HelmTargetState(StrEnum):
    ABSENT = "absent"
    SAME_DIGEST = "same_digest"
    CONFLICTING_DIGEST = "conflicting_digest"
    PRESENT = "present"


@dataclass(frozen=True, slots=True)
class HelmChartReference:
    repository: str
    name: str
    version: str

    def __post_init__(self) -> None:
        if fullmatch(_REPOSITORY_PATTERN, self.repository) is None:
            raise ValueError("repository must be a normalized Harbor repository path")
        if fullmatch(_CHART_NAME_PATTERN, self.name) is None:
            raise ValueError("name must be a normalized Helm chart name")
        if fullmatch(_VERSION_PATTERN, self.version) is None:
            raise ValueError("version contains unsupported characters")

    @property
    def harbor_repository(self) -> str:
        return f"{self.repository}/{self.name}"


@dataclass(frozen=True, slots=True)
class HelmPackageMetadata:
    path: Path
    name: str
    version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class HelmPullResult:
    source_digest: str | None
    package: HelmPackageMetadata


@dataclass(frozen=True, slots=True)
class HelmPushResult:
    package: HelmPackageMetadata
    target_digest: str
    source_digest: str | None
    digest_matches_source: bool | None


@dataclass(frozen=True, slots=True)
class HelmTargetInspection:
    state: HelmTargetState
    digest: str | None


@dataclass(frozen=True, slots=True)
class HelmProgressEvent:
    phase: HelmPhase
    repository: str
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class HelmCommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class HelmServiceError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class HelmCommandRunner(Protocol):
    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        env: Mapping[str, str],
        cwd: Path,
        stdin_bytes: bytes | None = None,
        redact_values: tuple[str, ...] = (),
    ) -> HelmCommandResult: ...


class AsyncioHelmCommandRunner:
    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        env: Mapping[str, str],
        cwd: Path,
        stdin_bytes: bytes | None = None,
        redact_values: tuple[str, ...] = (),
    ) -> HelmCommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise HelmServiceError("helm_io_error", "Не удалось открыть каналы Helm")

        if stdin_bytes is not None:
            if process.stdin is None:
                process.kill()
                await process.wait()
                raise HelmServiceError("helm_io_error", "Не удалось открыть stdin Helm")
            process.stdin.write(stdin_bytes)
            await process.stdin.drain()
            process.stdin.close()

        stdout_task = asyncio.create_task(_drain_limited(process.stdout, output_limit_bytes))
        stderr_task = asyncio.create_task(_drain_limited(process.stderr, output_limit_bytes))
        try:
            await asyncio.wait_for(process.wait(), timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
            raise HelmServiceError(
                "helm_timeout",
                "Операция Helm превысила допустимое время выполнения",
            ) from exc
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
            raise

        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        return HelmCommandResult(
            returncode=process.returncode or 0,
            stdout=_redact(stdout, redact_values),
            stderr=_redact(stderr, redact_values),
        )


async def _drain_limited(stream: asyncio.StreamReader, limit: int) -> str:
    retained = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        remaining = limit - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    text = bytes(retained).decode("utf-8", errors="replace")
    return text + ("\n[output truncated]" if truncated else "")


def _redact(text: str, values: Iterable[str]) -> str:
    redacted = text
    for value in values:
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


@dataclass(slots=True)
class _HelmSecurityContext:
    root: Path
    env: dict[str, str]
    ca_file: Path | None
    redact_values: tuple[str, ...]


class HelmOciService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        runner: HelmCommandRunner | None = None,
        progress: Callable[[HelmProgressEvent], None] | None = None,
        digest_resolver: Callable[[HelmChartReference], str | None] | None = None,
    ) -> None:
        self.settings = settings
        self.harbor_settings = HarborSettingsService(session, settings)
        self.runner = runner or AsyncioHelmCommandRunner()
        self.progress = progress
        self.workspace_root = settings.helm_workspace_root.resolve()
        self.digest_resolver = digest_resolver or self._resolve_harbor_digest

    async def pull_chart(
        self,
        chart: HelmChartReference,
        destination: Path,
    ) -> HelmPullResult:
        destination_path = self._validate_destination(destination)
        self._emit(HelmPhase.INSPECTING_SOURCE, chart)
        source_digest = await asyncio.to_thread(self.digest_resolver, chart)
        if source_digest is None:
            raise HelmServiceError("helm_source_not_found", "Helm chart/version не найден в Harbor")
        self._validate_optional_digest(source_digest)

        harbor = self.harbor_settings.resolve()
        registry = self._registry_host(harbor)
        destination_path.mkdir(parents=True, exist_ok=True)
        with self._security_context(harbor) as security:
            await self._login(registry, harbor, security, chart)
            self._emit(HelmPhase.PULLING, chart)
            argv = (
                self.settings.helm_binary,
                "pull",
                self._chart_oci_reference(registry, chart),
                "--version",
                chart.version,
                "--destination",
                str(destination_path),
                *self._tls_flags(harbor, security),
            )
            await self._run_checked(argv, security)
            package_path = self._discover_single_package(destination_path)
            package = await self._validate_package(package_path, chart, security)

        self._emit(HelmPhase.PULLED, chart)
        return HelmPullResult(source_digest=source_digest, package=package)

    async def validate_package(
        self,
        package: Path,
        expected: HelmChartReference,
    ) -> HelmPackageMetadata:
        package_path = self._validate_package_path(package)
        harbor = self.harbor_settings.resolve()
        with self._security_context(harbor) as security:
            return await self._validate_package(package_path, expected, security)

    async def inspect_target(
        self,
        chart: HelmChartReference,
        *,
        expected_digest: str | None = None,
    ) -> HelmTargetInspection:
        if expected_digest is not None:
            self._validate_optional_digest(expected_digest)
        self._emit(HelmPhase.INSPECTING_TARGET, chart)
        digest = await asyncio.to_thread(self.digest_resolver, chart)
        if digest is None:
            return HelmTargetInspection(HelmTargetState.ABSENT, None)
        self._validate_optional_digest(digest)
        if expected_digest is None:
            state = HelmTargetState.PRESENT
        elif digest == expected_digest:
            state = HelmTargetState.SAME_DIGEST
        else:
            state = HelmTargetState.CONFLICTING_DIGEST
        return HelmTargetInspection(state=state, digest=digest)

    async def push_chart(
        self,
        package: Path,
        target: HelmChartReference,
        *,
        source_digest: str | None = None,
    ) -> HelmPushResult:
        if source_digest is not None:
            self._validate_optional_digest(source_digest)
        preflight = await self.inspect_target(target, expected_digest=source_digest)
        if preflight.state is not HelmTargetState.ABSENT:
            raise HelmServiceError(
                "helm_target_exists",
                "TARGET уже содержит chart/version; решение конфликта выполняет import engine",
            )

        package_path = self._validate_package_path(package)
        harbor = self.harbor_settings.resolve()
        registry = self._registry_host(harbor)
        with self._security_context(harbor) as security:
            package_metadata = await self._validate_package(package_path, target, security)
            await self._login(registry, harbor, security, target)
            self._emit(HelmPhase.PUSHING, target)
            argv = (
                self.settings.helm_binary,
                "push",
                str(package_path),
                self._repository_oci_reference(registry, target),
                *self._tls_flags(harbor, security),
            )
            await self._run_checked(argv, security)

        self._emit(HelmPhase.VERIFYING_TARGET, target)
        target_digest = await asyncio.to_thread(self.digest_resolver, target)
        if target_digest is None:
            raise HelmServiceError(
                "helm_target_not_visible",
                "Harbor не подтвердил Helm artifact после успешного helm push",
            )
        self._validate_optional_digest(target_digest)
        self._emit(HelmPhase.PUSHED, target)
        return HelmPushResult(
            package=package_metadata,
            target_digest=target_digest,
            source_digest=source_digest,
            digest_matches_source=(
                target_digest == source_digest if source_digest is not None else None
            ),
        )

    async def _validate_package(
        self,
        package_path: Path,
        expected: HelmChartReference,
        security: _HelmSecurityContext,
    ) -> HelmPackageMetadata:
        self._emit(HelmPhase.VALIDATING, expected)
        self._validate_chart_archive_paths(package_path)
        result = await self._run_checked(
            (self.settings.helm_binary, "show", "chart", str(package_path)),
            security,
        )
        try:
            metadata = yaml.safe_load(result.stdout)
        except yaml.YAMLError as exc:
            raise HelmServiceError(
                "helm_chart_metadata_invalid",
                "Helm вернул некорректный Chart.yaml",
            ) from exc
        if not isinstance(metadata, dict):
            raise HelmServiceError(
                "helm_chart_metadata_invalid",
                "Helm не вернул объект metadata для chart package",
            )
        name = metadata.get("name")
        version = metadata.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise HelmServiceError(
                "helm_chart_metadata_invalid",
                "Chart metadata не содержит строковые name/version",
            )
        if name != expected.name or version != expected.version:
            raise HelmServiceError(
                "helm_chart_metadata_mismatch",
                "Chart package name/version не совпадают с manifest selection",
            )
        return HelmPackageMetadata(
            path=package_path,
            name=name,
            version=version,
            sha256=self._sha256_file(package_path),
        )

    async def _login(
        self,
        registry: str,
        harbor: EffectiveHarborSettings,
        security: _HelmSecurityContext,
        chart: HelmChartReference,
    ) -> None:
        if not harbor.username:
            return
        if not harbor.password:
            raise HelmServiceError(
                "helm_credential_incomplete",
                "Для Harbor username настроен без credential",
            )
        self._emit(HelmPhase.AUTHENTICATING, chart)
        argv: list[str] = [
            self.settings.helm_binary,
            "registry",
            "login",
            registry,
            "--username",
            harbor.username,
            "--password-stdin",
        ]
        if self._uses_plain_http(harbor):
            argv.append("--plain-http")
        elif harbor.verify_tls and security.ca_file is not None:
            argv.extend(("--ca-file", str(security.ca_file)))
        elif not harbor.verify_tls:
            argv.append("--insecure")
        await self._run_checked(
            tuple(argv),
            security,
            stdin_bytes=(harbor.password + "\n").encode(),
        )

    async def _run_checked(
        self,
        argv: tuple[str, ...],
        security: _HelmSecurityContext,
        *,
        stdin_bytes: bytes | None = None,
    ) -> HelmCommandResult:
        result = await self.runner.run(
            argv,
            timeout_seconds=self.settings.helm_timeout_seconds,
            output_limit_bytes=self.settings.helm_output_limit_bytes,
            env=security.env,
            cwd=security.root,
            stdin_bytes=stdin_bytes,
            redact_values=security.redact_values,
        )
        if result.returncode != 0:
            self._raise_command_error(result)
        return result

    @contextmanager
    def _security_context(self, harbor: EffectiveHarborSettings):
        self.settings.helm_temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(
            prefix="helm-task-",
            dir=self.settings.helm_temp_root,
        ) as temp_name:
            root = Path(temp_name)
            os.chmod(root, 0o700)
            config_home = root / "config"
            cache_home = root / "cache"
            data_home = root / "data"
            for directory in (config_home, cache_home, data_home):
                directory.mkdir(mode=0o700)
            env = {
                "PATH": os.environ.get("PATH", _DEFAULT_PATH),
                "HOME": str(root),
                "HELM_CONFIG_HOME": str(config_home),
                "HELM_CACHE_HOME": str(cache_home),
                "HELM_DATA_HOME": str(data_home),
                "HELM_REGISTRY_CONFIG": str(config_home / "registry.json"),
            }
            for key in _SAFE_LOCALE_ENV:
                value = os.environ.get(key)
                if value:
                    env[key] = value
            ca_file = None
            if harbor.verify_tls and harbor.ca_file is not None:
                if not harbor.ca_file.is_file():
                    raise HelmServiceError(
                        "helm_ca_unavailable",
                        "Настроенный CA-файл локального Harbor недоступен",
                    )
                ca_file = root / "harbor-ca.pem"
                shutil.copyfile(harbor.ca_file, ca_file)
                os.chmod(ca_file, 0o600)
            redact_values = (harbor.password,) if harbor.password else ()
            yield _HelmSecurityContext(root, env, ca_file, redact_values)

    def _resolve_harbor_digest(self, chart: HelmChartReference) -> str | None:
        project, repository = self._harbor_api_coordinates(chart)
        client = self.harbor_settings.build_client()
        try:
            return client.reference_digest(project, repository, chart.version)
        finally:
            client.close()

    @staticmethod
    def _harbor_api_coordinates(chart: HelmChartReference) -> tuple[str, str]:
        parts = chart.harbor_repository.split("/")
        project = parts[0]
        repository = "/".join(parts[1:])
        if not repository:
            raise HelmServiceError(
                "helm_repository_invalid",
                "Helm OCI path должен содержать project и chart repository",
            )
        return project, repository

    @staticmethod
    def _registry_host(harbor: EffectiveHarborSettings) -> str:
        if not harbor.url:
            raise HelmServiceError("harbor_not_configured", "Локальный Harbor не настроен")
        parsed = urlsplit(harbor.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
            raise HelmServiceError("harbor_url_invalid", "URL локального Harbor некорректен")
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise HelmServiceError(
                "harbor_url_invalid",
                "URL Harbor для OCI registry должен содержать только origin",
            )
        return parsed.netloc

    @staticmethod
    def _uses_plain_http(harbor: EffectiveHarborSettings) -> bool:
        if not harbor.url:
            return False
        return urlsplit(harbor.url).scheme == "http"

    @classmethod
    def _tls_flags(
        cls,
        harbor: EffectiveHarborSettings,
        security: _HelmSecurityContext,
    ) -> tuple[str, ...]:
        if cls._uses_plain_http(harbor):
            return ("--plain-http",)
        if not harbor.verify_tls:
            return ("--insecure-skip-tls-verify",)
        if security.ca_file is not None:
            return ("--ca-file", str(security.ca_file))
        return ()

    @staticmethod
    def _chart_oci_reference(registry: str, chart: HelmChartReference) -> str:
        return f"oci://{registry}/{chart.repository}/{chart.name}"

    @staticmethod
    def _repository_oci_reference(registry: str, chart: HelmChartReference) -> str:
        return f"oci://{registry}/{chart.repository}"

    def _validate_destination(self, path: Path) -> Path:
        resolved = path.resolve()
        self._require_under_workspace(resolved)
        if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
            raise HelmServiceError(
                "helm_destination_not_empty",
                "Каталог назначения Helm package должен отсутствовать или быть пустым",
            )
        return resolved

    def _validate_package_path(self, path: Path) -> Path:
        resolved = path.resolve()
        self._require_under_workspace(resolved)
        if not resolved.is_file() or resolved.suffix != ".tgz":
            raise HelmServiceError(
                "helm_package_invalid",
                "Ожидается существующий .tgz chart package внутри workspace",
            )
        return resolved

    def _require_under_workspace(self, path: Path) -> None:
        try:
            path.relative_to(self.workspace_root)
        except ValueError as exc:
            raise HelmServiceError(
                "helm_workspace_path_outside_root",
                "Helm workspace path находится вне разрешённого data root",
            ) from exc

    @staticmethod
    def _discover_single_package(destination: Path) -> Path:
        packages = sorted(
            item for item in destination.iterdir() if item.is_file() and item.suffix == ".tgz"
        )
        if len(packages) != 1:
            raise HelmServiceError(
                "helm_pull_output_invalid",
                "helm pull должен создать ровно один .tgz package",
            )
        return packages[0].resolve()

    @staticmethod
    def _validate_chart_archive_paths(package: Path) -> None:
        try:
            with tarfile.open(package, mode="r:gz") as archive:
                members = archive.getmembers()
        except (tarfile.TarError, OSError) as exc:
            raise HelmServiceError(
                "helm_package_invalid",
                "Chart package не является читаемым gzip tar archive",
            ) from exc
        if not members:
            raise HelmServiceError("helm_package_invalid", "Chart package пуст")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or not member.name
                or ".." in path.parts
                or "\\" in member.name
                or "\x00" in member.name
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                raise HelmServiceError(
                    "helm_package_unsafe_path",
                    "Chart package содержит небезопасный archive member",
                )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_optional_digest(value: str) -> None:
        if fullmatch(_DIGEST_PATTERN, value) is None:
            raise HelmServiceError(
                "helm_digest_invalid",
                "Harbor artifact digest должен иметь формат sha256",
            )

    @staticmethod
    def _raise_command_error(result: HelmCommandResult) -> None:
        lowered = result.stderr.casefold()
        auth_markers = ("unauthorized", "authentication required", "denied")
        if any(marker in lowered for marker in auth_markers):
            code = "helm_auth_failed"
            message = "Harbor отклонил аутентификацию Helm"
        elif any(marker in lowered for marker in ("x509", "certificate", "tls handshake")):
            code = "helm_tls_failed"
            message = "Helm не смог проверить TLS локального Harbor"
        elif any(marker in lowered for marker in ("not found", "manifest unknown")):
            code = "helm_not_found"
            message = "Запрошенный Helm OCI artifact не найден"
        else:
            code = "helm_command_failed"
            message = "Команда Helm завершилась с ошибкой"
        raise HelmServiceError(code, message)

    def _emit(self, phase: HelmPhase, chart: HelmChartReference) -> None:
        if self.progress is not None:
            self.progress(
                HelmProgressEvent(
                    phase=phase,
                    repository=chart.repository,
                    name=chart.name,
                    version=chart.version,
                )
            )
