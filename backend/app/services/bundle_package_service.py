from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.config import PortalContour, Settings
from app.domain.bundle import (
    ArtifactDescriptor,
    BundleManifest,
    BundleSource,
    ContainerImageArtifact,
    HelmChartArtifact,
)
from app.domain.protocol import canonical_manifest_bytes, validate_archive_members
from app.services.key_material import ed25519_public_key_fingerprint

_CRITICAL_FILES = ("manifest.json", "manifest.sig", "checksums.sha256")
_ALLOWED_PAYLOAD_ROOTS = {"images", "charts"}
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_DELIVERY_ID_RE = re.compile(r"^DELIVERY-[0-9]{8}-[A-Z0-9]{6,32}$")
_SAFE_TOKEN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(slots=True)
class BundlePackageError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ContainerImagePackageInput:
    repository: str
    reference: str
    source_digest: str
    source_path: Path
    payload_path: str


@dataclass(frozen=True, slots=True)
class HelmChartPackageInput:
    repository: str
    name: str
    version: str
    source_digest: str | None
    source_path: Path
    payload_path: str


PackageArtifactInput = ContainerImagePackageInput | HelmChartPackageInput


@dataclass(frozen=True, slots=True)
class BundleBuildResult:
    manifest: BundleManifest
    archive_path: Path
    sidecar_path: Path
    archive_sha256: str
    archive_size: int
    signing_key_fingerprint: str


@dataclass(frozen=True, slots=True)
class BundleVerificationResult:
    manifest: BundleManifest
    archive_sha256: str
    archive_size: int
    signing_key_fingerprint: str
    extracted_root: Path | None = None


@dataclass(frozen=True, slots=True)
class _PayloadStats:
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class _ArchiveInspection:
    members: tuple[tarfile.TarInfo, ...]
    file_members: dict[str, tarfile.TarInfo]


class BundlePackageService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.payload_root = settings.bundle_payload_root.resolve()
        self.temp_root = settings.bundle_temp_root.resolve()
        self.outgoing_root = settings.bundle_outgoing_root.resolve()
        self.extract_root = settings.bundle_extract_root.resolve()

    def allocate_delivery_id(self, now: datetime | None = None) -> str:
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("delivery timestamp must be timezone-aware")
        date = current.astimezone(UTC).strftime("%Y%m%d")
        random_part = "".join(
            secrets.choice(_SAFE_TOKEN_ALPHABET) for _ in range(12)
        )
        return f"DELIVERY-{date}-{random_part}"

    def build_bundle(
        self,
        *,
        source: BundleSource,
        created_by: str,
        artifacts: Sequence[PackageArtifactInput],
        delivery_id: str | None = None,
        created_at: datetime | None = None,
        comment: str | None = None,
    ) -> BundleBuildResult:
        if self.settings.portal_contour is not PortalContour.SOURCE:
            raise BundlePackageError(
                "bundle_build_wrong_contour",
                "Подписанный bundle можно создавать только в контуре SOURCE",
            )
        if not artifacts:
            raise BundlePackageError(
                "bundle_artifacts_empty",
                "Bundle должен содержать артефакты",
            )
        if source.contour != "SOURCE":
            raise BundlePackageError(
                "bundle_source_invalid",
                "Bundle source contour должен быть SOURCE",
            )

        now = (created_at or datetime.now(UTC)).astimezone(UTC)
        identifier = delivery_id or self.allocate_delivery_id(now)
        if _DELIVERY_ID_RE.fullmatch(identifier) is None:
            raise BundlePackageError(
                "bundle_delivery_id_invalid",
                "delivery_id имеет неверный формат",
            )

        self._ensure_private_directory(self.temp_root)
        self.outgoing_root.mkdir(parents=True, exist_ok=True)
        archive_name = f"{identifier}.htp.tar.gz"
        final_archive = self.outgoing_root / archive_name
        final_sidecar = self.outgoing_root / f"{archive_name}.sha256"
        if final_archive.exists() or final_sidecar.exists():
            raise BundlePackageError(
                "bundle_delivery_exists",
                "Bundle с таким delivery_id уже опубликован",
            )

        reservation = self._reserve_delivery(identifier)
        try:
            private_key = self._load_signing_private_key()
            public_key = private_key.public_key()
            fingerprint = ed25519_public_key_fingerprint(public_key)
            with tempfile.TemporaryDirectory(
                prefix="bundle-build-",
                dir=self.temp_root,
            ) as name:
                workspace = Path(name)
                os.chmod(workspace, 0o700)
                content_root = workspace / "content"
                content_root.mkdir(mode=0o700)
                staged_inputs = self._stage_payloads(content_root, artifacts)
                checksum_entries = self._checksum_payload_files(
                    content_root,
                    staged_inputs,
                )
                descriptors = self._build_descriptors(
                    staged_inputs,
                    checksum_entries,
                )
                manifest = self._build_manifest(
                    identifier=identifier,
                    created_at=now,
                    created_by=created_by,
                    source=source,
                    comment=comment,
                    descriptors=descriptors,
                )
                manifest_bytes = canonical_manifest_bytes(manifest)
                (content_root / "manifest.json").write_bytes(manifest_bytes)
                (content_root / "manifest.sig").write_bytes(
                    private_key.sign(manifest_bytes)
                )
                (content_root / "checksums.sha256").write_bytes(
                    self._serialize_checksums(checksum_entries)
                )

                temporary_archive = workspace / archive_name
                self._write_deterministic_archive(content_root, temporary_archive)
                archive_sha = self._sha256_file(temporary_archive)
                archive_size = temporary_archive.stat().st_size
                self._check_archive_size(archive_size)
                self._verify_bundle(
                    temporary_archive,
                    sidecar_path=None,
                    trusted_keys=(public_key,),
                    extract_to=None,
                )
                self._atomic_publish(
                    temporary_archive,
                    final_archive,
                    final_sidecar,
                    archive_sha,
                )
        except BundlePackageError:
            raise
        except OSError as exc:
            raise BundlePackageError(
                "bundle_io_error",
                "Ошибка файловой системы при создании bundle",
            ) from exc
        finally:
            reservation.unlink(missing_ok=True)

        return BundleBuildResult(
            manifest=manifest,
            archive_path=final_archive,
            sidecar_path=final_sidecar,
            archive_sha256=archive_sha,
            archive_size=archive_size,
            signing_key_fingerprint=fingerprint,
        )

    def verify_bundle(
        self,
        archive_path: Path,
        *,
        sidecar_path: Path | None = None,
        extract_to: Path | None = None,
    ) -> BundleVerificationResult:
        return self._verify_bundle(
            archive_path,
            sidecar_path=sidecar_path,
            trusted_keys=self._load_trusted_public_keys(),
            extract_to=extract_to,
        )

    def _verify_bundle(
        self,
        archive_path: Path,
        *,
        sidecar_path: Path | None,
        trusted_keys: Sequence[Ed25519PublicKey],
        extract_to: Path | None,
    ) -> BundleVerificationResult:
        archive = archive_path.resolve()
        if not archive.is_file() or archive.is_symlink():
            raise BundlePackageError("bundle_not_found", "Bundle archive не найден")
        archive_size = archive.stat().st_size
        self._check_archive_size(archive_size)
        archive_sha = self._sha256_file(archive)
        if sidecar_path is not None:
            self._verify_sidecar(sidecar_path, archive, archive_sha)

        extracted: Path | None = None
        try:
            with tarfile.open(archive, mode="r:gz") as bundle:
                inspection = self._inspect_archive(bundle, archive_size)
                manifest_bytes = self._read_small_member(
                    bundle,
                    "manifest.json",
                    inspection,
                )
                signature = self._read_small_member(
                    bundle,
                    "manifest.sig",
                    inspection,
                )
                checksums_bytes = self._read_small_member(
                    bundle,
                    "checksums.sha256",
                    inspection,
                )
                manifest_payload = self._parse_canonical_manifest(manifest_bytes)
                self._require_supported_major(manifest_payload)
                self._validate_json_schema(manifest_payload)
                fingerprint = self._verify_signature(
                    manifest_bytes,
                    signature,
                    trusted_keys,
                )
                try:
                    manifest = BundleManifest.model_validate(manifest_payload)
                except ValidationError as exc:
                    raise BundlePackageError(
                        "bundle_manifest_invalid",
                        "Manifest не прошёл semantic validation",
                    ) from exc

                checksums = self._parse_checksums(checksums_bytes)
                self._validate_payload_layout(manifest, inspection, checksums)
                self._verify_payload_checksums(bundle, inspection, checksums)
                self._verify_descriptor_metadata(manifest, checksums, inspection)
                if extract_to is not None:
                    extracted = self._extract_verified_members(
                        bundle,
                        inspection,
                        extract_to,
                    )
        except BundlePackageError:
            raise
        except (tarfile.TarError, OSError) as exc:
            raise BundlePackageError(
                "bundle_archive_invalid",
                "Bundle archive повреждён или не читается",
            ) from exc

        return BundleVerificationResult(
            manifest=manifest,
            archive_sha256=archive_sha,
            archive_size=archive_size,
            signing_key_fingerprint=fingerprint,
            extracted_root=extracted,
        )

    @staticmethod
    def _build_manifest(
        *,
        identifier: str,
        created_at: datetime,
        created_by: str,
        source: BundleSource,
        comment: str | None,
        descriptors: list[ArtifactDescriptor],
    ) -> BundleManifest:
        try:
            return BundleManifest(
                schema_version="1.0",
                delivery_id=identifier,
                created_at=created_at,
                created_by=created_by,
                source=source,
                comment=comment,
                artifacts=descriptors,
            )
        except ValidationError as exc:
            raise BundlePackageError(
                "bundle_manifest_invalid",
                "Не удалось сформировать корректный manifest",
            ) from exc

    def _stage_payloads(
        self,
        workspace: Path,
        artifacts: Sequence[PackageArtifactInput],
    ) -> tuple[PackageArtifactInput, ...]:
        roots: list[PurePosixPath] = []
        staged: list[PackageArtifactInput] = []
        for artifact in artifacts:
            payload_path = self._validate_payload_destination(artifact)
            if any(self._paths_overlap(payload_path, existing) for existing in roots):
                raise BundlePackageError(
                    "bundle_payload_overlap",
                    "Payload paths разных артефактов не должны пересекаться",
                )
            roots.append(payload_path)
            source = artifact.source_path.resolve()
            self._require_under_payload_root(source)
            destination = workspace.joinpath(*payload_path.parts)
            if isinstance(artifact, ContainerImagePackageInput):
                if not source.is_dir() or source.is_symlink():
                    raise BundlePackageError(
                        "bundle_image_payload_invalid",
                        "Container image payload должен быть каталогом OCI image-layout",
                    )
                self._copy_directory_snapshot(source, destination)
            else:
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or source.suffix != ".tgz"
                ):
                    raise BundlePackageError(
                        "bundle_chart_payload_invalid",
                        "Helm payload должен быть обычным .tgz файлом",
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                os.chmod(destination, 0o600)
            staged.append(artifact)
        return tuple(staged)

    def _copy_directory_snapshot(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        count = 0
        total = 0
        for current, dir_names, file_names in os.walk(source, followlinks=False):
            current_path = Path(current)
            relative = current_path.relative_to(source)
            target_dir = destination / relative
            target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in sorted(dir_names):
                if (current_path / name).is_symlink():
                    raise BundlePackageError(
                        "bundle_payload_unsafe_type",
                        "Symlink внутри payload запрещён",
                    )
            for name in sorted(file_names):
                child = current_path / name
                info = child.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise BundlePackageError(
                        "bundle_payload_unsafe_type",
                        "Payload содержит неподдерживаемый тип файла",
                    )
                count += 1
                total += info.st_size
                self._check_payload_limits(count, total)
                target = target_dir / name
                shutil.copyfile(child, target)
                os.chmod(target, 0o600)

    def _checksum_payload_files(
        self,
        workspace: Path,
        artifacts: Sequence[PackageArtifactInput],
    ) -> dict[str, tuple[str, int]]:
        entries: dict[str, tuple[str, int]] = {}
        for artifact in artifacts:
            root = PurePosixPath(artifact.payload_path)
            staged = workspace.joinpath(*root.parts)
            if staged.is_file():
                candidates = (staged,)
            else:
                candidates = tuple(
                    sorted(path for path in staged.rglob("*") if path.is_file())
                )
            if not candidates:
                raise BundlePackageError(
                    "bundle_payload_empty",
                    "Payload не содержит файлов",
                )
            for file_path in candidates:
                relative = file_path.relative_to(workspace).as_posix()
                self._validate_member_name(relative)
                entries[relative] = (
                    self._sha256_file(file_path),
                    file_path.stat().st_size,
                )
        return dict(sorted(entries.items()))

    def _build_descriptors(
        self,
        artifacts: Sequence[PackageArtifactInput],
        checksums: dict[str, tuple[str, int]],
    ) -> list[ArtifactDescriptor]:
        descriptors: list[ArtifactDescriptor] = []
        for artifact in artifacts:
            stats = self._payload_stats(artifact.payload_path, checksums)
            if isinstance(artifact, ContainerImagePackageInput):
                descriptor: ArtifactDescriptor = ContainerImageArtifact(
                    repository=artifact.repository,
                    reference=artifact.reference,
                    source_digest=artifact.source_digest,
                    payload_path=artifact.payload_path,
                    payload_sha256=stats.sha256,
                    payload_size=stats.size,
                )
            else:
                descriptor = HelmChartArtifact(
                    repository=artifact.repository,
                    name=artifact.name,
                    version=artifact.version,
                    source_digest=artifact.source_digest,
                    payload_path=artifact.payload_path,
                    payload_sha256=stats.sha256,
                    payload_size=stats.size,
                )
            descriptors.append(descriptor)
        return descriptors

    def _write_deterministic_archive(
        self,
        workspace: Path,
        archive_path: Path,
    ) -> None:
        entries = sorted(path for path in workspace.rglob("*") if not path.is_symlink())
        with archive_path.open("wb") as raw:
            with gzip.GzipFile(
                fileobj=raw,
                mode="wb",
                filename="",
                mtime=0,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as archive:
                    for path in entries:
                        relative = path.relative_to(workspace).as_posix()
                        self._validate_member_name(relative)
                        info = tarfile.TarInfo(
                            relative + ("/" if path.is_dir() else "")
                        )
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mtime = 0
                        if path.is_dir():
                            info.type = tarfile.DIRTYPE
                            info.mode = 0o755
                            archive.addfile(info)
                        elif path.is_file():
                            info.size = path.stat().st_size
                            info.mode = 0o644
                            with path.open("rb") as stream:
                                archive.addfile(info, stream)
                        else:
                            raise BundlePackageError(
                                "bundle_payload_unsafe_type",
                                "Неподдерживаемый тип файла при упаковке",
                            )

    def _inspect_archive(
        self,
        archive: tarfile.TarFile,
        archive_size: int,
    ) -> _ArchiveInspection:
        members = tuple(archive.getmembers())
        if len(members) > self.settings.bundle_max_member_count:
            raise BundlePackageError(
                "bundle_member_limit_exceeded",
                "Bundle содержит слишком много archive members",
            )
        try:
            validate_archive_members(members)
        except ValueError as exc:
            raise BundlePackageError("bundle_archive_unsafe", str(exc)) from exc

        file_members: dict[str, tarfile.TarInfo] = {}
        total_size = 0
        for member in members:
            normalized = member.name.rstrip("/") if member.isdir() else member.name
            self._validate_member_name(normalized)
            self._validate_top_level(normalized)
            if member.isfile():
                total_size += member.size
                if total_size > self.settings.bundle_max_extracted_bytes:
                    raise BundlePackageError(
                        "bundle_extracted_size_exceeded",
                        "Распакованный размер bundle превышает настроенный лимит",
                    )
                file_members[normalized] = member
        if archive_size > 0:
            ratio = total_size / archive_size
            if ratio > self.settings.bundle_max_compression_ratio:
                raise BundlePackageError(
                    "bundle_compression_ratio_exceeded",
                    "Bundle отклонён по лимиту коэффициента распаковки",
                )
        return _ArchiveInspection(members=members, file_members=file_members)

    @staticmethod
    def _validate_top_level(name: str) -> None:
        if name in _CRITICAL_FILES:
            return
        first = PurePosixPath(name).parts[0]
        if first not in _ALLOWED_PAYLOAD_ROOTS:
            raise BundlePackageError(
                "bundle_archive_unsafe",
                f"Archive member вне разрешённого top-level: {name}",
            )

    def _read_small_member(
        self,
        archive: tarfile.TarFile,
        path: str,
        inspection: _ArchiveInspection,
    ) -> bytes:
        member = inspection.file_members.get(path)
        if member is None:
            raise BundlePackageError(
                "bundle_required_file_invalid",
                f"{path} должен быть файлом",
            )
        if member.size > self.settings.bundle_max_metadata_bytes:
            raise BundlePackageError(
                "bundle_metadata_too_large",
                f"{path} превышает допустимый размер",
            )
        stream = archive.extractfile(member)
        if stream is None:
            raise BundlePackageError(
                "bundle_archive_invalid",
                f"Не удалось прочитать {path}",
            )
        payload = stream.read(self.settings.bundle_max_metadata_bytes + 1)
        if len(payload) > self.settings.bundle_max_metadata_bytes:
            raise BundlePackageError(
                "bundle_metadata_too_large",
                f"{path} превышает допустимый размер",
            )
        return payload

    def _parse_canonical_manifest(self, manifest_bytes: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundlePackageError(
                "bundle_manifest_invalid",
                "manifest.json содержит неверный JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise BundlePackageError(
                "bundle_manifest_invalid",
                "manifest.json должен быть JSON object",
            )
        canonical = json.dumps(
            self._drop_nulls(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if manifest_bytes != canonical:
            raise BundlePackageError(
                "bundle_manifest_not_canonical",
                "manifest.json не соответствует canonical serialization v1",
            )
        return payload

    @staticmethod
    def _drop_nulls(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: BundlePackageService._drop_nulls(item)
                for key, item in value.items()
                if item is not None
            }
        if isinstance(value, list):
            return [BundlePackageService._drop_nulls(item) for item in value]
        return value

    @staticmethod
    def _require_supported_major(payload: dict[str, Any]) -> None:
        version = payload.get("schema_version")
        if not isinstance(version, str) or not version.startswith("1."):
            raise BundlePackageError(
                "bundle_schema_unsupported",
                "Неподдерживаемая major-версия bundle schema",
            )

    def _validate_json_schema(self, payload: dict[str, Any]) -> None:
        validator = Draft202012Validator(
            self._load_manifest_schema(),
            format_checker=FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            raise BundlePackageError(
                "bundle_schema_invalid",
                "manifest.json не прошёл JSON Schema validation",
            )

    @staticmethod
    def _load_manifest_schema() -> dict[str, Any]:
        schema_file = resources.files("app.domain").joinpath(
            "manifest-v1.schema.json"
        )
        try:
            payload = json.loads(schema_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BundlePackageError(
                "bundle_schema_runtime_invalid",
                "Runtime JSON Schema недоступна или повреждена",
            ) from exc
        if not isinstance(payload, dict):
            raise BundlePackageError(
                "bundle_schema_runtime_invalid",
                "Runtime JSON Schema некорректна",
            )
        return payload

    def _verify_signature(
        self,
        manifest_bytes: bytes,
        signature: bytes,
        trusted_keys: Sequence[Ed25519PublicKey],
    ) -> str:
        if len(signature) != 64:
            raise BundlePackageError(
                "bundle_signature_invalid",
                "Ed25519 signature имеет неверный размер",
            )
        for key in trusted_keys:
            try:
                key.verify(signature, manifest_bytes)
            except InvalidSignature:
                continue
            return ed25519_public_key_fingerprint(key)
        raise BundlePackageError(
            "bundle_signature_untrusted",
            "Подпись bundle не подтверждается настроенными trusted SOURCE keys",
        )

    def _parse_checksums(self, payload: bytes) -> dict[str, str]:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundlePackageError(
                "bundle_checksums_invalid",
                "checksums.sha256 должен быть UTF-8",
            ) from exc
        entries: dict[str, str] = {}
        for line in text.splitlines():
            if not line:
                raise BundlePackageError(
                    "bundle_checksums_invalid",
                    "Пустая checksum-строка запрещена",
                )
            if len(line) < 67 or line[64:66] != "  ":
                raise BundlePackageError(
                    "bundle_checksums_invalid",
                    "Неверный формат checksum-строки",
                )
            digest = line[:64]
            path = line[66:]
            if _SHA256_RE.fullmatch(digest) is None:
                raise BundlePackageError(
                    "bundle_checksums_invalid",
                    "Неверный SHA-256 в checksums",
                )
            self._validate_member_name(path)
            if path in _CRITICAL_FILES or path in entries:
                raise BundlePackageError(
                    "bundle_checksums_invalid",
                    "Duplicate/critical checksum path",
                )
            entries[path] = digest
        if not entries:
            raise BundlePackageError(
                "bundle_checksums_invalid",
                "checksums.sha256 не содержит payload",
            )
        return entries

    def _validate_payload_layout(
        self,
        manifest: BundleManifest,
        inspection: _ArchiveInspection,
        checksums: dict[str, str],
    ) -> None:
        archive_payload_files = set(inspection.file_members) - set(_CRITICAL_FILES)
        if archive_payload_files != set(checksums):
            raise BundlePackageError(
                "bundle_payload_layout_mismatch",
                "Checksum file set не совпадает с payload files в archive",
            )
        roots: list[PurePosixPath] = []
        covered: set[str] = set()
        for artifact in manifest.artifacts:
            root = PurePosixPath(artifact.payload_path)
            if any(self._paths_overlap(root, existing) for existing in roots):
                raise BundlePackageError(
                    "bundle_payload_overlap",
                    "Manifest содержит пересекающиеся payload paths",
                )
            roots.append(root)
            root_name = root.as_posix()
            matches = {
                path
                for path in checksums
                if path == root_name or path.startswith(root_name + "/")
            }
            if not matches:
                raise BundlePackageError(
                    "bundle_payload_missing",
                    f"Payload отсутствует: {root_name}",
                )
            covered.update(matches)
            if isinstance(artifact, ContainerImageArtifact):
                if root.parts[0] != "images" or root_name in inspection.file_members:
                    raise BundlePackageError(
                        "bundle_image_payload_invalid",
                        "Container image payload должен быть каталогом внутри images/",
                    )
            else:
                if root.parts[0] != "charts" or not root_name.endswith(".tgz"):
                    raise BundlePackageError(
                        "bundle_chart_payload_invalid",
                        "Helm payload должен быть .tgz файлом внутри charts/",
                    )
                if root_name not in inspection.file_members:
                    raise BundlePackageError(
                        "bundle_chart_payload_invalid",
                        "Helm payload descriptor должен указывать на файл",
                    )
        if covered != set(checksums):
            raise BundlePackageError(
                "bundle_payload_undeclared",
                "Archive содержит payload, не объявленный в manifest",
            )

    def _verify_payload_checksums(
        self,
        archive: tarfile.TarFile,
        inspection: _ArchiveInspection,
        checksums: dict[str, str],
    ) -> None:
        for path, expected in checksums.items():
            member = inspection.file_members[path]
            stream = archive.extractfile(member)
            if stream is None:
                raise BundlePackageError(
                    "bundle_archive_invalid",
                    f"Не удалось прочитать {path}",
                )
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != expected:
                raise BundlePackageError(
                    "bundle_payload_checksum_mismatch",
                    f"SHA-256 payload не совпадает: {path}",
                )

    def _verify_descriptor_metadata(
        self,
        manifest: BundleManifest,
        checksums: dict[str, str],
        inspection: _ArchiveInspection,
    ) -> None:
        combined = {
            path: (checksums[path], inspection.file_members[path].size)
            for path in checksums
        }
        for artifact in manifest.artifacts:
            stats = self._payload_stats(artifact.payload_path, combined)
            if (
                stats.sha256 != artifact.payload_sha256
                or stats.size != artifact.payload_size
            ):
                raise BundlePackageError(
                    "bundle_payload_metadata_mismatch",
                    f"Payload metadata не совпадает: {artifact.payload_path}",
                )

    @staticmethod
    def _payload_stats(
        payload_path: str,
        checksums: dict[str, tuple[str, int]],
    ) -> _PayloadStats:
        prefix = payload_path + "/"
        matches = [
            (path, digest, size)
            for path, (digest, size) in checksums.items()
            if path == payload_path or path.startswith(prefix)
        ]
        if not matches:
            raise BundlePackageError(
                "bundle_payload_missing",
                f"Payload отсутствует: {payload_path}",
            )
        matches.sort(key=lambda item: item[0])
        total_size = sum(item[2] for item in matches)
        if len(matches) == 1 and matches[0][0] == payload_path:
            return _PayloadStats(sha256=matches[0][1], size=total_size)
        canonical = "".join(
            f"{digest}  {path}\n" for path, digest, _size in matches
        ).encode()
        return _PayloadStats(
            sha256=hashlib.sha256(canonical).hexdigest(),
            size=total_size,
        )

    def _extract_verified_members(
        self,
        archive: tarfile.TarFile,
        inspection: _ArchiveInspection,
        destination: Path,
    ) -> Path:
        target = destination.resolve()
        try:
            target.relative_to(self.extract_root)
        except ValueError as exc:
            raise BundlePackageError(
                "bundle_extract_path_outside_root",
                "Каталог распаковки должен находиться внутри configured extract root",
            ) from exc
        if target.exists():
            raise BundlePackageError(
                "bundle_extract_destination_exists",
                "Каталог распаковки должен отсутствовать",
            )
        self.extract_root.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix="bundle-extract-", dir=target.parent)
        )
        os.chmod(temporary, 0o700)
        try:
            for member in inspection.members:
                name = member.name.rstrip("/") if member.isdir() else member.name
                path = temporary.joinpath(*PurePosixPath(name).parts)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BundlePackageError(
                        "bundle_archive_invalid",
                        f"Не удалось извлечь {name}",
                    )
                with path.open("xb") as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
                os.chmod(path, 0o600)
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return target

    def _reserve_delivery(self, identifier: str) -> Path:
        reservation = self.outgoing_root / f".{identifier}.reserve"
        try:
            descriptor = os.open(
                reservation,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise BundlePackageError(
                "bundle_delivery_exists",
                "Bundle с таким delivery_id уже создаётся",
            ) from exc
        os.close(descriptor)
        return reservation

    def _atomic_publish(
        self,
        temporary_archive: Path,
        final_archive: Path,
        final_sidecar: Path,
        archive_sha: str,
    ) -> None:
        self.outgoing_root.mkdir(parents=True, exist_ok=True)
        sidecar_tmp = self.outgoing_root / (
            f".{final_sidecar.name}.{secrets.token_hex(6)}.tmp"
        )
        published_archive = False
        try:
            os.replace(temporary_archive, final_archive)
            published_archive = True
            sidecar_tmp.write_text(
                f"{archive_sha}  {final_archive.name}\n",
                encoding="utf-8",
            )
            os.chmod(sidecar_tmp, 0o600)
            os.replace(sidecar_tmp, final_sidecar)
        except Exception:
            final_sidecar.unlink(missing_ok=True)
            if published_archive:
                final_archive.unlink(missing_ok=True)
            raise
        finally:
            sidecar_tmp.unlink(missing_ok=True)

    def _verify_sidecar(
        self,
        sidecar_path: Path,
        archive: Path,
        actual_sha: str,
    ) -> None:
        sidecar = sidecar_path.resolve()
        if not sidecar.is_file() or sidecar.is_symlink():
            raise BundlePackageError(
                "bundle_sidecar_invalid",
                "Bundle sidecar не найден",
            )
        if sidecar.stat().st_size > self.settings.bundle_max_metadata_bytes:
            raise BundlePackageError(
                "bundle_sidecar_invalid",
                "Bundle sidecar слишком большой",
            )
        try:
            text = sidecar.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BundlePackageError(
                "bundle_sidecar_invalid",
                "Bundle sidecar не читается",
            ) from exc
        expected = f"{actual_sha}  {archive.name}\n"
        if text != expected:
            raise BundlePackageError(
                "bundle_sidecar_checksum_mismatch",
                "Bundle sidecar не подтверждает SHA-256 archive",
            )

    def _load_signing_private_key(self) -> Ed25519PrivateKey:
        path = self.settings.bundle_signing_private_key_file.absolute()
        self._validate_private_key_file(path)
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise BundlePackageError(
                "bundle_signing_key_invalid",
                "SOURCE signing private key не читается как PEM Ed25519 key",
            ) from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise BundlePackageError(
                "bundle_signing_key_invalid",
                "SOURCE signing key должен быть Ed25519 private key",
            )
        return key

    def _load_trusted_public_keys(self) -> tuple[Ed25519PublicKey, ...]:
        directory = self.settings.bundle_trusted_public_keys_dir.absolute()
        if not directory.is_dir() or directory.is_symlink():
            raise BundlePackageError(
                "bundle_trust_not_configured",
                "Каталог trusted SOURCE public keys не настроен",
            )
        key_files = sorted(directory.glob("*.pem"))
        if not key_files or len(key_files) > self.settings.bundle_max_trusted_keys:
            raise BundlePackageError(
                "bundle_trust_not_configured",
                "Количество trusted SOURCE public keys недопустимо",
            )
        keys: list[Ed25519PublicKey] = []
        for path in key_files:
            self._validate_trusted_key_file(path)
            try:
                key = serialization.load_pem_public_key(path.read_bytes())
            except (OSError, ValueError, TypeError) as exc:
                raise BundlePackageError(
                    "bundle_trusted_key_invalid",
                    f"Trusted public key некорректен: {path.name}",
                ) from exc
            if not isinstance(key, Ed25519PublicKey):
                raise BundlePackageError(
                    "bundle_trusted_key_invalid",
                    f"Trusted key должен быть Ed25519 public key: {path.name}",
                )
            keys.append(key)
        return tuple(keys)

    def _validate_private_key_file(self, path: Path) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise BundlePackageError(
                "bundle_signing_key_not_configured",
                "SOURCE signing private key не настроен",
            ) from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise BundlePackageError(
                "bundle_signing_key_invalid",
                "Signing key должен быть обычным файлом",
            )
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise BundlePackageError(
                "bundle_signing_key_permissions",
                "Signing private key должен быть недоступен group/other",
            )
        if info.st_size > self.settings.bundle_key_material_max_bytes:
            raise BundlePackageError(
                "bundle_signing_key_invalid",
                "SOURCE signing private key превышает допустимый размер",
            )

    def _validate_trusted_key_file(self, path: Path) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise BundlePackageError(
                "bundle_trusted_key_invalid",
                f"Trusted public key недоступен: {path.name}",
            ) from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise BundlePackageError(
                "bundle_trusted_key_invalid",
                f"Trusted key должен быть обычным файлом: {path.name}",
            )
        if info.st_size > self.settings.bundle_key_material_max_bytes:
            raise BundlePackageError(
                "bundle_trusted_key_invalid",
                f"Trusted public key превышает допустимый размер: {path.name}",
            )

    def _validate_payload_destination(
        self,
        artifact: PackageArtifactInput,
    ) -> PurePosixPath:
        path = PurePosixPath(artifact.payload_path)
        normalized = path.as_posix()
        self._validate_member_name(normalized)
        if normalized != artifact.payload_path or len(path.parts) < 2:
            raise BundlePackageError(
                "bundle_payload_path_invalid",
                "Payload path должен быть нормализованным relative POSIX path",
            )
        if isinstance(artifact, ContainerImagePackageInput):
            if path.parts[0] != "images":
                raise BundlePackageError(
                    "bundle_payload_path_invalid",
                    "Container image payload должен находиться внутри images/",
                )
        elif path.parts[0] != "charts" or not path.name.endswith(".tgz"):
            raise BundlePackageError(
                "bundle_payload_path_invalid",
                "Helm payload должен быть .tgz внутри charts/",
            )
        return path

    def _validate_member_name(self, name: str) -> None:
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
            or "\\" in name
            or "\x00" in name
            or "\n" in name
            or "\r" in name
            or len(name.encode("utf-8")) > self.settings.bundle_max_path_bytes
        ):
            raise BundlePackageError(
                "bundle_archive_unsafe",
                f"Небезопасный bundle path: {name!r}",
            )

    def _require_under_payload_root(self, path: Path) -> None:
        try:
            path.relative_to(self.payload_root)
        except ValueError as exc:
            raise BundlePackageError(
                "bundle_payload_outside_root",
                "Payload source находится вне configured payload root",
            ) from exc

    def _check_archive_size(self, size: int) -> None:
        if size <= 0 or size > self.settings.bundle_max_archive_bytes:
            raise BundlePackageError(
                "bundle_archive_size_exceeded",
                "Размер bundle archive недопустим или превышает configured limit",
            )

    def _check_payload_limits(self, count: int, size: int) -> None:
        if count > self.settings.bundle_max_member_count:
            raise BundlePackageError(
                "bundle_member_limit_exceeded",
                "Payload содержит слишком много файлов",
            )
        if size > self.settings.bundle_max_extracted_bytes:
            raise BundlePackageError(
                "bundle_extracted_size_exceeded",
                "Payload превышает configured extracted-size limit",
            )

    @staticmethod
    def _serialize_checksums(entries: dict[str, tuple[str, int]]) -> bytes:
        return "".join(
            f"{digest}  {path}\n"
            for path, (digest, _size) in entries.items()
        ).encode()

    @staticmethod
    def _paths_overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
        return left == right or left in right.parents or right in left.parents

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
