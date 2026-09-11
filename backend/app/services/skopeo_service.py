from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from re import fullmatch
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings, HarborSettingsService

_DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"
_REPOSITORY_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
_TAG_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}"
_OCI_REFERENCE = "image"


class SkopeoPhase(StrEnum):
    INSPECTING_SOURCE = "inspecting_source"
    EXPORTING = "exporting"
    EXPORTED = "exported"
    INSPECTING_TARGET = "inspecting_target"
    IMPORTING = "importing"
    VERIFYING_TARGET = "verifying_target"
    VERIFIED = "verified"


class TargetState(StrEnum):
    ABSENT = "absent"
    SAME_DIGEST = "same_digest"
    CONFLICTING_DIGEST = "conflicting_digest"
    PRESENT = "present"


@dataclass(frozen=True, slots=True)
class ImageReference:
    repository: str
    reference: str

    def __post_init__(self) -> None:
        if fullmatch(_REPOSITORY_PATTERN, self.repository) is None:
            raise ValueError("repository must be a normalized Harbor repository path")
        if fullmatch(_TAG_PATTERN, self.reference) is None and fullmatch(
            _DIGEST_PATTERN, self.reference
        ) is None:
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
class SkopeoProgressEvent:
    phase: SkopeoPhase
    repository: str
    reference: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class SkopeoServiceError(Exception):
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
    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise SkopeoServiceError("skopeo_io_error", "Не удалось открыть каналы Skopeo")

        stdout_task = asyncio.create_task(_drain_limited(process.stdout, output_limit_bytes))
        stderr_task = asyncio.create_task(_drain_limited(process.stderr, output_limit_bytes))
        try:
            await asyncio.wait_for(process.wait(), timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
            raise SkopeoServiceError(
                "skopeo_timeout",
                "Операция Skopeo превысила допустимое время выполнения",
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


class SkopeoService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        runner: CommandRunner | None = None,
        progress: Callable[[SkopeoProgressEvent], None] | None = None,
    ) -> None:
        self.settings = settings
        self.harbor_settings = HarborSettingsService(session, settings)
        self.runner = runner or AsyncioCommandRunner()
        self.progress = progress
        self.payload_root = settings.skopeo_payload_root.resolve()

    async def inspect_image(self, image: ImageReference) -> ImageInspection:
        self._emit(SkopeoPhase.INSPECTING_SOURCE, image)
        harbor = self.harbor_settings.resolve()
        with self._security_context(harbor) as security:
            return await self._inspect_registry(image, harbor, security)

    async def export_image(self, image: ImageReference, destination: Path) -> ExportResult:
        payload_path = self._validate_export_path(destination)
        self._emit(SkopeoPhase.INSPECTING_SOURCE, image)
        harbor = self.harbor_settings.resolve()
        with self._security_context(harbor) as security:
            source = await self._inspect_registry(image, harbor, security)
            payload_path.mkdir(parents=True, exist_ok=True)
            self._emit(SkopeoPhase.EXPORTING, image)
            argv = (
                self.settings.skopeo_binary,
                "copy",
                "--all",
                "--preserve-digests",
                *self._registry_flags("src", harbor, security),
                self._docker_transport(image, harbor),
                self._oci_transport(payload_path),
            )
            await self._run_checked(argv, security)
            self._validate_oci_layout(payload_path)
            payload = await self._inspect_oci(payload_path)
            if payload.digest != source.digest:
                raise SkopeoServiceError(
                    "skopeo_digest_mismatch",
                    "Digest OCI payload не совпадает с исходным Harbor digest",
                )
            self._emit(SkopeoPhase.EXPORTED, image)
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
            raise SkopeoServiceError(
                "skopeo_payload_digest_mismatch",
                "Digest локального OCI payload не совпадает с manifest expectation",
            )

        harbor = self.harbor_settings.resolve()
        with self._security_context(harbor) as security:
            self._emit(SkopeoPhase.IMPORTING, target)
            argv = (
                self.settings.skopeo_binary,
                "copy",
                "--all",
                "--preserve-digests",
                *self._registry_flags("dest", harbor, security),
                self._oci_transport(payload_path),
                self._docker_transport(target, harbor),
            )
            await self._run_checked(argv, security)
            self._emit(SkopeoPhase.VERIFYING_TARGET, target)
            observed = await self._inspect_registry(target, harbor, security)
            if observed.digest != expected_digest:
                raise SkopeoServiceError(
                    "skopeo_digest_mismatch",
                    "Digest в TARGET Harbor не совпадает с ожидаемым digest",
                )
            self._emit(SkopeoPhase.VERIFIED, target)
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
        self._emit(SkopeoPhase.INSPECTING_TARGET, image)
        harbor = self.harbor_settings.resolve()
        with self._security_context(harbor) as security:
            result = await self._run(
                (
                    self.settings.skopeo_binary,
                    "inspect",
                    *self._registry_flags("inspect", harbor, security),
                    self._docker_transport(image, harbor),
                ),
                security,
            )
            if result.returncode != 0:
                if self._is_not_found(result.stderr):
                    return TargetInspection(TargetState.ABSENT, None)
                self._raise_command_error(result)
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
        harbor: EffectiveHarborSettings,
        security: _SecurityContext,
    ) -> ImageInspection:
        result = await self._run_checked(
            (
                self.settings.skopeo_binary,
                "inspect",
                *self._registry_flags("inspect", harbor, security),
                self._docker_transport(image, harbor),
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
            self._raise_command_error(result)
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
        harbor: EffectiveHarborSettings,
        security: _SecurityContext,
    ) -> tuple[str, ...]:
        prefix = "" if direction == "inspect" else f"{direction}-"
        flags: list[str] = []
        if security.auth_file is not None:
            flags.extend((f"--{prefix}authfile", str(security.auth_file)))
        flags.append(f"--{prefix}tls-verify={'true' if harbor.verify_tls else 'false'}")
        if harbor.verify_tls and security.cert_dir is not None:
            flags.extend((f"--{prefix}cert-dir", str(security.cert_dir)))
        return tuple(flags)

    def _docker_transport(
        self,
        image: ImageReference,
        harbor: EffectiveHarborSettings,
    ) -> str:
        registry = self._registry_host(harbor)
        separator = "@" if image.reference.startswith("sha256:") else ":"
        return f"docker://{registry}/{image.repository}{separator}{image.reference}"

    @staticmethod
    def _registry_host(harbor: EffectiveHarborSettings) -> str:
        if not harbor.url:
            raise SkopeoServiceError("harbor_not_configured", "Локальный Harbor не настроен")
        parsed = urlsplit(harbor.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
            raise SkopeoServiceError("harbor_url_invalid", "URL локального Harbor некорректен")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise SkopeoServiceError(
                "harbor_url_invalid",
                "URL Harbor для OCI registry не должен содержать path/query/userinfo",
            )
        return parsed.netloc

    @staticmethod
    def _oci_transport(payload_path: Path) -> str:
        if ":" in str(payload_path):
            raise SkopeoServiceError(
                "skopeo_payload_path_invalid",
                "Путь OCI payload содержит неподдерживаемый символ ':'",
            )
        return f"oci:{payload_path}:{_OCI_REFERENCE}"

    def _validate_export_path(self, path: Path) -> Path:
        resolved = path.resolve()
        self._require_under_payload_root(resolved)
        if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
            raise SkopeoServiceError(
                "skopeo_destination_not_empty",
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
            raise SkopeoServiceError(
                "skopeo_payload_path_outside_root",
                "OCI payload должен находиться внутри разрешённого data root",
            ) from exc

    @staticmethod
    def _validate_oci_layout(path: Path) -> None:
        if not path.is_dir() or not (path / "oci-layout").is_file() or not (path / "index.json").is_file():
            raise SkopeoServiceError(
                "skopeo_payload_invalid",
                "Каталог не является OCI image-layout payload",
            )
        blobs = path / "blobs" / "sha256"
        if not blobs.is_dir():
            raise SkopeoServiceError(
                "skopeo_payload_invalid",
                "OCI image-layout не содержит blobs/sha256",
            )

    @staticmethod
    def _parse_inspection(payload: str) -> ImageInspection:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SkopeoServiceError(
                "skopeo_invalid_output",
                "Skopeo вернул некорректный JSON inspect",
            ) from exc
        if not isinstance(data, dict):
            raise SkopeoServiceError("skopeo_invalid_output", "Skopeo inspect вернул не объект")
        digest = data.get("Digest")
        if not isinstance(digest, str) or fullmatch(_DIGEST_PATTERN, digest) is None:
            raise SkopeoServiceError(
                "skopeo_invalid_output",
                "Skopeo inspect не вернул корректный sha256 digest",
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
        if fullmatch(_DIGEST_PATTERN, value) is None:
            raise ValueError("expected_digest must be a sha256 digest")

    @staticmethod
    def _is_not_found(stderr: str) -> bool:
        lowered = stderr.casefold()
        return any(
            marker in lowered
            for marker in ("manifest unknown", "name unknown", "not found", "status code: 404")
        )

    @staticmethod
    def _raise_command_error(result: CommandResult) -> None:
        lowered = result.stderr.casefold()
        if any(marker in lowered for marker in ("unauthorized", "authentication required", "denied")):
            code = "skopeo_auth_failed"
            message = "Harbor отклонил аутентификацию Skopeo"
        elif any(marker in lowered for marker in ("x509", "certificate", "tls handshake")):
            code = "skopeo_tls_failed"
            message = "Skopeo не смог проверить TLS локального Harbor"
        elif SkopeoService._is_not_found(result.stderr):
            code = "skopeo_not_found"
            message = "Запрошенный OCI artifact не найден"
        else:
            code = "skopeo_command_failed"
            message = "Skopeo завершился с ошибкой"
        raise SkopeoServiceError(code, message)

    def _security_context(self, harbor: EffectiveHarborSettings):
        service = self

        class Context:
            def __enter__(self) -> _SecurityContext:
                temp_root = service.settings.skopeo_temp_root.resolve()
                temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.temp_dir = tempfile.TemporaryDirectory(prefix="htp-skopeo-", dir=temp_root)
                root = Path(self.temp_dir.name)
                os.chmod(root, 0o700)
                auth_file: Path | None = None
                cert_dir: Path | None = None
                redact_values: list[str] = []

                if harbor.username is not None or harbor.password is not None:
                    username = harbor.username or ""
                    password = harbor.password or ""
                    registry = service._registry_host(harbor)
                    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
                    auth_file = root / "auth.json"
                    auth_file.write_text(
                        json.dumps({"auths": {registry: {"auth": encoded}}}),
                        encoding="utf-8",
                    )
                    os.chmod(auth_file, 0o600)
                    redact_values.extend((username, password, encoded))

                if harbor.verify_tls and harbor.ca_file is not None:
                    if not harbor.ca_file.is_file():
                        raise SkopeoServiceError(
                            "harbor_ca_unavailable",
                            "Настроенный CA-файл локального Harbor недоступен",
                        )
                    cert_dir = root / "certs"
                    cert_dir.mkdir(mode=0o700)
                    ca_target = cert_dir / "ca.crt"
                    shutil.copyfile(harbor.ca_file, ca_target)
                    os.chmod(ca_target, 0o600)

                self.security = _SecurityContext(
                    root=root,
                    auth_file=auth_file,
                    cert_dir=cert_dir,
                    redact_values=tuple(redact_values),
                )
                return self.security

            def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
                self.temp_dir.cleanup()

        return Context()

    def _emit(self, phase: SkopeoPhase, image: ImageReference) -> None:
        if self.progress is not None:
            self.progress(SkopeoProgressEvent(phase, image.repository, image.reference))
