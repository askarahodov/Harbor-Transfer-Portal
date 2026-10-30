from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings, HarborSettingsService

_DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"
_REPOSITORY_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
_TAG_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}"
_PERSISTED_DIAGNOSTIC_LIMIT = 512
_SAFE_LOCALE_ENV = ("LANG", "LC_ALL", "LC_CTYPE")


class NexusPhase(str):
    INSPECTING_SOURCE = "inspecting_source"
    EXPORTING = "exporting"
    EXPORTED = "exported"
    INSPECTING_TARGET = "inspecting_target"
    IMPORTING = "importing"
    VERIFYING_TARGET = "verifying_target"
    VERIFIED = "verified"


class TargetState(str):
    ABSENT = "absent"
    SAME_DIGEST = "same_digest"
    CONFLICTING_DIGEST = "conflicting_digest"
    PRESENT = "present"


@dataclass(frozen=True, slots=True)
class ImageReference:
    repository: str
    reference: str

    def __post_init__(self) -> None:
        import re
        _REPOSITORY_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
        _TAG_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}"
        _DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"
        if not re.fullmatch(_REPOSITORY_PATTERN, self.repository):
            raise ValueError("repository must be a normalized registry repository path")
        if not (re.fullmatch(_TAG_PATTERN, self.reference) or re.fullmatch(r"sha256:[a-f0-9]{64}", self.reference)):
            raise ValueError("reference must be an OCI tag or sha256 digest")


@dataclass(frozen=True, slots=True)
class ImageInspection:
    digest: str
    media_type: str | None = None
    architecture: str | None = None
    os: str | None = None


@dataclass(frozen=True, slots=True)
class ExportResult:
    source_digest: str
    payload_digest: str
    payload_path: Path


@dataclass(frozen=True, slots=True)
class ImportResult:
    expected_digest: str
    target_digest: str
    verified: bool


@dataclass(frozen=True, slots=True)
class TargetInspection:
    state: TargetState
    digest: str | None


@dataclass(frozen=True, slots=True)
class NexusProgressEvent:
    phase: str
    repository: str
    reference: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class NexusServiceError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class CommandRunner(Protocol):
    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ) -> CommandResult: ...


class AsyncioCommandRunner:
    def __init__(self, temp_root: Path | None = None) -> None:
        self.temp_root = temp_root.resolve() if temp_root is not None else None

    @staticmethod
    def _environment(root: Path) -> dict[str, str]:
        home = root / "home"
        config_home = root / "xdg-config"
        cache_home = root / "xdg-cache"
        runtime_dir = root / "xdg-runtime"
        temp_dir = root / "tmp"
        for directory in (home, config_home, cache_home, runtime_dir, temp_dir):
            directory.mkdir(mode=0o700)

        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_CACHE_HOME": str(cache_home),
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "TMPDIR": str(temp_dir),
        }
        for name in _SAFE_LOCALE_ENV:
            value = os.environ.get(name)
            if value:
                environment[name] = value
        return environment

    @staticmethod
    def _argv_for_isolated_cwd(argv: tuple[str, ...]) -> tuple[str, ...]:
        executable = Path(argv[0])
        if executable.is_absolute() or len(executable.parts) == 1:
            return argv
        return (os.path.abspath(argv[0]), *argv[1:])

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ) -> CommandResult:
        isolated_argv = self._argv_for_isolated_cwd(argv)
        if self.temp_root is not None:
            self.temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="htp-nexus-exec-", dir=self.temp_root) as raw_root:
            root = Path(raw_root)
            os.chmod(root, 0o700)
            process = await asyncio.create_subprocess_exec(
                *isolated_argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root),
                env=self._environment(root),
            )
            if process.stdout is None or process.stderr is None:
                process.kill()
                await process.wait()
                raise NexusServiceError("nexus_io_error", "Не удалось открыть каналы Nexus")
            stdout_task = asyncio.create_task(_drain_limited(process.stdout, output_limit_bytes))
            stderr_task = asyncio.create_task(_drain_limited(process.stderr, output_limit_bytes))
            try:
                await asyncio.wait_for(process.wait(), timeout_seconds)
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)
                raise NexusServiceError(
                    "nexus_timeout",
                    "Операция Nexus превысила допустимое время выполнения",
                ) from exc
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)
                raise

            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            return CommandResult(
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
class _SecurityContext:
    root: Path
    auth_file: Path | None
    cert_dir: Path | None
    redact_values: tuple[str, ...]


class NexusService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        nexus_profile_id: str | None = None,
        runner: CommandRunner | None = None,
        progress: Callable[[NexusProgressEvent], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.nexus_profile_id = nexus_profile_id
        self.runner = runner or AsyncioCommandRunner(settings.skopeo_temp_root)
        self.progress = progress
        self.payload_root = settings.skopeo_payload_root.resolve()

    def _resolve_nexus(self) -> EffectiveHarborSettings:
        if self.nexus_profile_id is None:
            return EffectiveHarborSettings(
                url=str(self.settings.nexus_url) if self.settings.nexus_url else None,
                username=self.settings.nexus_user,
                password=self.settings.nexus_password.get_secret_value() if self.settings.nexus_password else None,
                verify_tls=self.settings.nexus_verify_tls,
                ca_file=self.settings.nexus_ca_file,
            )
        return EffectiveHarborSettings(
            url=str(self.settings.nexus_url) if self.settings.nexus_url else None,
            username=self.settings.nexus_user,
            password=self.settings.nexus_password.get_secret_value() if self.settings.nexus_password else None,
            verify_tls=self.settings.nexus_verify_tls,
            ca_file=self.settings.nexus_ca_file,
        )

    async def inspect_image(self, image: ImageReference) -> ImageInspection:
        self._emit(NexusPhase.INSPECTING_SOURCE, image)
        nexus = self._resolve_nexus()
        with self._security_context(nexus) as security:
            return await self._inspect_registry(image, nexus, security)

    async def export_image(self, image: ImageReference, destination: Path) -> ExportResult:
        payload_path = self._validate_export_path(destination)
        self._emit(NexusPhase.INSPECTING_SOURCE, image)
        nexus = self._resolve_nexus()
        with self._security_context(nexus) as security:
            source = await self._inspect_registry(image, nexus, security)
            payload_path.mkdir(parents=True, exist_ok=True)
            self._emit(NexusPhase.EXPORTING, image)
            argv = (
                self.settings.skopeo_binary,
                "copy",
                "--all",
                "--preserve-digests",
                *self._registry_flags("src", nexus, security),
                self._docker_transport(image, nexus),
                self._oci_transport(payload_path),
            )
            await self._run_checked(argv, security)
            self._validate_oci_layout(payload_path)
            payload = await self._inspect_oci(payload_path)
            if payload.digest != source.digest:
                raise NexusServiceError(
                    "skopeo_digest_mismatch",
                    "Digest OCI payload не совпадает с исходным Nexus digest",
                )
            self._emit(NexusPhase.EXPORTED, image)
            return ExportResult(
                source_digest=source.digest,
                payload_digest=payload.digest,
                payload_path=payload_path,
            )

    async def import_image(
        self,
        payload: Path,
        target: ImageReference,
        *,
        expected_digest: str,
    ) -> ImportResult:
        self._validate_digest(expected_digest)
        payload_path = self._validate_import_path(payload)
        payload_inspection = await self._inspect_oci(payload_path)
        if payload_inspection.digest != expected_digest:
            raise NexusServiceError(
                "skopeo_payload_digest_mismatch",
                "Digest локального OCI payload не совпадает с manifest expectation",
            )

        nexus = self._resolve_nexus()
        with self._security_context(nexus) as security:
            self._emit(NexusPhase.IMPORTING, target)
            argv = (
                self.settings.skopeo_binary,
                "copy",
                "--all",
                "--preserve-digests",
                *self._registry_flags("dest", nexus, security),
                self._oci_transport(payload_path),
                self._docker_transport(target, nexus),
            )
            await self._run_checked(argv, security)
            self._emit(NexusPhase.VERIFYING_TARGET, target)
            observed = await self._inspect_registry(target, nexus, security)
            if observed.digest != expected_digest:
                raise NexusServiceError(
                    "skopeo_digest_mismatch",
                    "Digest в TARGET Nexus не совпадает с ожидаемым digest",
                )
            self._emit(NexusPhase.VERIFIED, target)
            return ImportResult(
                expected_digest=expected_digest,
                target_digest=observed.digest,
                verified=True,
            )

    async def inspect_target(
        self,
        image: ImageReference,
        *,
        expected_digest: str | None = None,
    ) -> TargetInspection:
        if expected_digest is not None:
            self._validate_digest(expected_digest)
        self._emit(NexusPhase.INSPECTING_TARGET, image)
        nexus = self._resolve_nexus()
        with self._security_context(nexus) as security:
            result = await self._run(
                (
                    self.settings.skopeo_binary,
                    "inspect",
                    *self._registry_flags("inspect", nexus, security),
                    self._docker_transport(image, nexus),
                ),
                security,
            )
            if result.returncode != 0:
                if self._is_not_found(result.stderr):
                    return TargetInspection(TargetState.ABSENT, None)
                self._raise_command_error(result, security)
            inspection = self._parse_inspection(result.stdout)
            if expected_digest is None:
                state = TargetState.PRESENT
            elif inspection.digest == expected_digest:
                state = TargetState.SAME_DIGEST
            else:
                state = TargetState.CONFLICTING_DIGEST
            return TargetInspection(state=state, digest=inspection.digest)

    async def _inspect_registry(
        self,
        image: ImageReference,
        nexus: EffectiveHarborSettings,
        security: _SecurityContext,
    ) -> ImageInspection:
        result = await self._run_checked(
            (
                self.settings.skopeo_binary,
                "inspect",
                *self._registry_flags("inspect", nexus, security),
                self._docker_transport(image, nexus),
            ),
            security,
        )
        return self._parse_inspection(result.stdout)

    async def _inspect_oci(self, payload_path: Path) -> ImageInspection:
        result = await self._run_checked(
            (
                self.settings.skopeo_binary,
                "inspect",
                self._oci_transport(payload_path),
            ),
            _SecurityContext(Path(), None, None, ()),
        )
        return self._parse_inspection(result.stdout)

    async def _run_checked(
        self,
        argv: tuple[str, ...],
        security: _SecurityContext,
    ) -> CommandResult:
        result = await self._run(argv, security)
        if result.returncode != 0:
            self._raise_command_error(result, security)
        return result

    async def _run(
        self,
        argv: tuple[str, ...],
        security: _SecurityContext,
    ) -> CommandResult:
        return await self.runner.run(
            argv,
            timeout_seconds=self.settings.skopeo_timeout_seconds,
            output_limit_bytes=self.settings.skopeo_output_limit_bytes,
            redact_values=security.redact_values,
        )

    def _registry_flags(
        self,
        direction: str,
        nexus: EffectiveHarborSettings,
        security: _SecurityContext,
    ) -> tuple[str, ...]:
        prefix = "" if direction == "inspect" else f"{direction}-"
        flags: list[str] = []
        if security.auth_file is not None:
            flags.extend((f"--{prefix}authfile", str(security.auth_file)))
        flags.append(f"--{prefix}tls-verify={'true' if nexus.verify_tls else 'false'}")
        if nexus.verify_tls and security.cert_dir is not None:
            flags.extend((f"--{prefix}cert-dir", str(security.cert_dir)))
        return tuple(flags)

    def _docker_transport(
        self,
        image: ImageReference,
        nexus: EffectiveHarborSettings,
    ) -> str:
        registry = self._registry_host(nexus)
        separator = "@" if image.reference.startswith("sha256:") else ":"
        return f"docker://{registry}/{image.repository}{separator}{image.reference}"

    @staticmethod
    def _registry_host(nexus: EffectiveHarborSettings) -> str:
        if not nexus.url:
            raise NexusServiceError("nexus_not_configured", "Локальный Nexus не настроен")
        parsed = urlsplit(str(nexus.url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
            raise NexusServiceError("nexus_url_invalid", "URL локального Nexus некорректен")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise NexusServiceError(
                "nexus_url_invalid",
                "URL Nexus для OCI registry не должен содержать path/query/userinfo",
            )
        return parsed.netloc

    @staticmethod
    def _oci_transport(payload_path: Path) -> str:
        if ":" in str(payload_path):
            raise NexusServiceError(
                "nexus_payload_path_invalid",
                "Путь OCI payload содержит неподдерживаемый символ ':'",
            )
        return f"oci:{payload_path}"

    def _validate_export_path(self, path: Path) -> Path:
        resolved = path.resolve()
        self._require_under_payload_root(resolved)
        if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
            raise NexusServiceError(
                "nexus_destination_not_empty",
                "Каталог назначения OCI payload должен отсутствовать или быть пустым",
            )
        return resolved

    def _validate_import_path(self, path: Path) -> Path:
        resolved = path.resolve()
        self._require_under_payload_root(resolved)
        self._validate_oci_layout(resolved)
        return resolved

    def _require_under_payload_root(self, path: Path) -> None:
        try:
            path.relative_to(self.payload_root)
        except ValueError as exc:
            raise NexusServiceError(
                "nexus_payload_path_outside_root",
                "OCI payload должен находиться внутри разрешённого data root",
            ) from exc

    @staticmethod
    def _validate_oci_layout(path: Path) -> None:
        required_files_exist = (
            path.is_dir()
            and (path / "oci-layout").is_file()
            and (path / "index.json").is_file()
        )
        if not required_files_exist:
            raise NexusServiceError(
                "nexus_payload_invalid",
                "Каталог не является OCI image-layout payload",
            )
        blobs = path / "blobs" / "sha256"
        if not blobs.is_dir():
            raise NexusServiceError(
                "nexus_payload_invalid",
                "OCI image-layout не содержит blobs/sha256",
            )

    @staticmethod
    def _parse_inspection(payload: str) -> ImageInspection:
        import json
        import re
        _DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise NexusServiceError(
                "nexus_invalid_output",
                "Nexus вернул некорректный JSON inspect",
            ) from exc
        if not isinstance(data, dict):
            raise NexusServiceError("nexus_invalid_output", "Nexus inspect не вернул объект")
        digest = data.get("Digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise NexusServiceError(
                "nexus_invalid_output",
                "Nexus inspect не вернул корректный sha256 digest",
            )
        return ImageInspection(
            digest=digest,
            media_type=data.get("MediaType") if isinstance(data.get("MediaType"), str) else None,
            architecture=(
                data.get("Architecture") if isinstance(data.get("Architecture"), str) else None
            ),
            os=data.get("Os") if isinstance(data.get("Os"), str) else None,
        )

    @staticmethod
    def _validate_digest(value: str) -> None:
        import re
        _DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("expected_digest must be a sha256 digest")

    @staticmethod
    def _is_not_found(stderr: str) -> bool:
        lowered = stderr.casefold()
        return any(
            marker in lowered
            for marker in ("manifest unknown", "name unknown", "not found", "status code: 404")
        )

    @staticmethod
    def _diagnostic(stderr: str, security: _SecurityContext) -> str:
        redacted = _redact(stderr, security.redact_values)
        compact = " ".join(redacted.split())
        if len(compact) <= _PERSISTED_DIAGNOSTIC_LIMIT:
            return compact
        return compact[: _PERSISTED_DIAGNOSTIC_LIMIT - 3] + "..."

    @staticmethod
    def _raise_command_error(
        result: CommandResult,
        security: _SecurityContext,
    ) -> None:
        stderr = _redact(result.stderr, security.redact_values)
        lowered = stderr.casefold()
        auth_markers = ("unauthorized", "authentication required", "denied")
        include_diagnostic = False
        if any(marker in lowered for marker in auth_markers):
            code = "nexus_auth_failed"
            message = "Nexus отклонил аутентификацию"
        elif any(marker in lowered for marker in ("x509", "certificate", "tls handshake")):
            code = "nexus_tls_failed"
            message = "Не удалось проверить TLS локального Nexus"
        elif any(
            marker in lowered
            for marker in (
                "i/o timeout",
                "connection timed out",
                "connect: operation timed out",
                "context deadline exceeded",
            )
        ):
            code = "nexus_registry_connect_timeout"
            message = (
                "Не удалось установить сетевое соединение с локальным Nexus: "
                "истекло время ожидания"
            )
        elif any(
            marker in lowered
            for marker in (
                "connection refused",
                "no route to host",
                "network is unreachable",
                "connection reset by peer",
            )
        ):
            code = "nexus_registry_connection_failed"
            message = "Не удалось подключиться к локальному Nexus по сети"
        elif NexusService._is_not_found(stderr):
            code = "nexus_not_found"
            message = "Запрошенный OCI artifact не найден"
        elif any(
            marker in lowered
            for marker in (
                "instructed to preserve digests",
                "cannot preserve digest",
                "cannot preserve the digest",
            )
        ):
            code = "nexus_digest_preservation_failed"
            message = "Не удалось сохранить OCI digest при копировании"
            include_diagnostic = True
        else:
            code = "nexus_command_failed"
            message = "Nexus завершился с ошибкой"
            include_diagnostic = True

        if include_diagnostic:
            diagnostic = NexusService._diagnostic(stderr, security)
            if diagnostic:
                message = f"{message}: {diagnostic}"
        raise NexusServiceError(code, message)

    def _security_context(self, nexus: EffectiveHarborSettings):
        service = self

        class Context:
            def __enter__(self) -> _SecurityContext:
                temp_root = service.settings.skopeo_temp_root.resolve()
                temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.temp_dir = tempfile.TemporaryDirectory(prefix="htp-nexus-", dir=temp_root)
                root = Path(self.temp_dir.name)
                os.chmod(root, 0o700)
                auth_file: Path | None = None
                cert_dir: Path | None = None
                redact_values: list[str] = []

                if nexus.username is not None or nexus.password is not None:
                    username = nexus.username or ""
                    password = nexus.password or ""
                    registry = service._registry_host(nexus)
                    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
                    auth_file = root / "auth.json"
                    auth_file.write_text(
                        json.dumps({"auths": {registry: {"auth": encoded}}}),
                        encoding="utf-8",
                    )
                    os.chmod(auth_file, 0o600)
                    redact_values.extend((username, password, encoded))

                if nexus.verify_tls and nexus.ca_file is not None:
                    if not nexus.ca_file.is_file():
                        raise NexusServiceError(
                            "nexus_ca_unavailable",
                            "Настроенный CA-файл локального Nexus недоступен",
                        )
                    cert_dir = root / "certs"
                    cert_dir.mkdir(mode=0o700)
                    ca_target = cert_dir / "ca.crt"
                    shutil.copyfile(nexus.ca_file, ca_target)
                    os.chmod(ca_target, 0o600)

                self.security = _SecurityContext(
                    root=root,
                    auth_file=auth_file,
                    cert_dir=cert_dir,
                    redact_values=tuple(redact_values),
                )
                return self.security

            def __exit__(self, exc_type, exc, tb) -> None:
                self.temp_dir.cleanup()

        return Context()

    def _emit(self, phase: str, image: ImageReference) -> None:
        if self.progress is not None:
            self.progress(NexusProgressEvent(phase, image.repository, image.reference))