from __future__ import annotations

import gzip
import io
import json
import tarfile
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config import PortalContour, Settings
from app.services.key_management import (
    KeyManagementError,
    KeyManagementService,
    KeyMutation,
)
from app.services.key_material import ed25519_public_key_fingerprint

_TRUST_PACKAGE_KIND = "harbor-transfer-portal-source-identity"
_TRUST_PACKAGE_SCHEMA = "1.0"
_TRUST_PACKAGE_FILES = (
    "source-signing-public.pem",
    "identity.json",
    "fingerprint.sha256",
)


@dataclass(slots=True)
class TrustPackageError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class TrustPackageBuildResult:
    payload: bytes
    fingerprint: str
    filename: str


class SourceTrustPackageService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.keys = KeyManagementService(settings)

    def build(self) -> TrustPackageBuildResult:
        self._require_source()
        try:
            public = self.keys.signing_public_key()
        except KeyManagementError as exc:
            raise TrustPackageError(exc.code, exc.message) from exc
        return self._build(public)

    def build_pending(self) -> TrustPackageBuildResult:
        self._require_source()
        try:
            public = self.keys.pending_signing_public_key()
        except KeyManagementError as exc:
            raise TrustPackageError(exc.code, exc.message) from exc
        return self._build(public)

    def _build(self, public) -> TrustPackageBuildResult:
        identity = {
            "algorithm": "Ed25519",
            "fingerprint": public.fingerprint,
            "kind": _TRUST_PACKAGE_KIND,
            "schema_version": _TRUST_PACKAGE_SCHEMA,
        }
        files = {
            "source-signing-public.pem": public.pem,
            "identity.json": (
                json.dumps(
                    identity,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
            "fingerprint.sha256": f"{public.fingerprint}\n".encode("ascii"),
        }
        payload = self._archive(files)
        if len(payload) > self.settings.bundle_trust_package_max_bytes:
            raise TrustPackageError(
                "trust_package_too_large",
                "SOURCE trust package превышает допустимый размер",
            )
        short = public.fingerprint.removeprefix("sha256:")[:16]
        return TrustPackageBuildResult(
            payload=payload,
            fingerprint=public.fingerprint,
            filename=f"source-trust-{short}.htp-trust.tar.gz",
        )

    def import_package(self, payload: bytes) -> KeyMutation:
        self._require_target()
        if not payload or len(payload) > self.settings.bundle_trust_package_max_bytes:
            raise TrustPackageError(
                "trust_package_size_invalid",
                "Trust package пуст или превышает допустимый размер",
            )

        files = self._read_archive(payload)
        public_pem = files["source-signing-public.pem"]
        identity_bytes = files["identity.json"]
        fingerprint_bytes = files["fingerprint.sha256"]

        try:
            identity = json.loads(identity_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrustPackageError(
                "trust_package_metadata_invalid",
                "identity.json некорректен",
            ) from exc
        if not isinstance(identity, dict) or set(identity) != {
            "algorithm",
            "fingerprint",
            "kind",
            "schema_version",
        }:
            raise TrustPackageError(
                "trust_package_metadata_invalid",
                "identity.json содержит неподдерживаемую структуру",
            )
        if (
            identity.get("kind") != _TRUST_PACKAGE_KIND
            or identity.get("schema_version") != _TRUST_PACKAGE_SCHEMA
            or identity.get("algorithm") != "Ed25519"
        ):
            raise TrustPackageError(
                "trust_package_metadata_invalid",
                "SOURCE identity metadata имеет неподдерживаемую версию или алгоритм",
            )

        try:
            key = serialization.load_pem_public_key(public_pem)
        except (ValueError, TypeError) as exc:
            raise TrustPackageError(
                "trust_package_public_key_invalid",
                "Trust package не содержит корректный Ed25519 public key",
            ) from exc
        if not isinstance(key, Ed25519PublicKey):
            raise TrustPackageError(
                "trust_package_public_key_invalid",
                "Trust package должен содержать Ed25519 public key",
            )
        fingerprint = ed25519_public_key_fingerprint(key)
        try:
            fingerprint_file = fingerprint_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise TrustPackageError(
                "trust_package_fingerprint_mismatch",
                "fingerprint.sha256 некорректен",
            ) from exc
        if (
            identity.get("fingerprint") != fingerprint
            or fingerprint_file != f"{fingerprint}\n"
        ):
            raise TrustPackageError(
                "trust_package_fingerprint_mismatch",
                "Fingerprint trust package не совпадает с фактическим public key",
            )

        try:
            existing = {
                item.fingerprint: item.enabled for item in self.keys.list_trusted_keys()
            }
            if fingerprint in existing:
                if existing[fingerprint]:
                    return KeyMutation(action="unchanged", fingerprint=fingerprint)
                return self.keys.set_trusted_key_enabled(fingerprint, True)
            return self.keys.add_trusted_public_key(public_pem.decode("ascii"))
        except (UnicodeDecodeError, KeyManagementError) as exc:
            if isinstance(exc, KeyManagementError):
                raise TrustPackageError(exc.code, exc.message) from exc
            raise TrustPackageError(
                "trust_package_public_key_invalid",
                "Public key trust package должен быть ASCII PEM",
            ) from exc

    def _read_archive(self, payload: bytes) -> dict[str, bytes]:
        raw_archive = self._bounded_decompress(payload)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:") as archive:
                members = archive.getmembers()
                names = [member.name for member in members]
                if (
                    len(members) != len(_TRUST_PACKAGE_FILES)
                    or len(set(names)) != len(names)
                    or set(names) != set(_TRUST_PACKAGE_FILES)
                ):
                    raise TrustPackageError(
                        "trust_package_layout_invalid",
                        "Trust package должен содержать ровно ожидаемый public identity набор",
                    )
                result: dict[str, bytes] = {}
                for member in members:
                    if not member.isfile() or member.issym() or member.islnk():
                        raise TrustPackageError(
                            "trust_package_layout_invalid",
                            "Trust package содержит неподдерживаемый тип entry",
                        )
                    if member.size < 1 or member.size > self.settings.bundle_key_material_max_bytes:
                        raise TrustPackageError(
                            "trust_package_entry_size_invalid",
                            "Trust package entry пуст или превышает допустимый размер",
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise TrustPackageError(
                            "trust_package_layout_invalid",
                            "Trust package entry не читается",
                        )
                    result[member.name] = stream.read(
                        self.settings.bundle_key_material_max_bytes + 1
                    )
                    if len(result[member.name]) > self.settings.bundle_key_material_max_bytes:
                        raise TrustPackageError(
                            "trust_package_entry_size_invalid",
                            "Trust package entry превышает допустимый размер",
                        )
                return result
        except TrustPackageError:
            raise
        except (tarfile.TarError, OSError) as exc:
            raise TrustPackageError(
                "trust_package_archive_invalid",
                "Trust package повреждён или не читается",
            ) from exc

    def _bounded_decompress(self, payload: bytes) -> bytes:
        limit = self.settings.bundle_trust_package_max_bytes
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as compressed:
                raw = compressed.read(limit + 1)
        except (OSError, EOFError) as exc:
            raise TrustPackageError(
                "trust_package_archive_invalid",
                "Trust package gzip повреждён или не читается",
            ) from exc
        if len(raw) > limit:
            raise TrustPackageError(
                "trust_package_decompressed_size_invalid",
                "Распакованный trust package превышает допустимый размер",
            )
        return raw

    @staticmethod
    def _archive(files: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                for name in _TRUST_PACKAGE_FILES:
                    payload = files[name]
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    info.mode = 0o600
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(payload))
        return buffer.getvalue()

    def _require_source(self) -> None:
        if self.settings.portal_contour is not PortalContour.SOURCE:
            raise TrustPackageError(
                "trust_package_wrong_contour",
                "Trust package можно сформировать только в режиме SOURCE",
            )

    def _require_target(self) -> None:
        if self.settings.portal_contour is not PortalContour.TARGET:
            raise TrustPackageError(
                "trust_package_wrong_contour",
                "SOURCE trust package можно импортировать только в режиме TARGET",
            )
