from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.config import PortalContour, Settings
from app.domain.bundle import BundleManifest
from app.services.key_management import KeyManagementError, KeyManagementService
from app.services.key_material import ed25519_public_key_fingerprint

_HANDOFF_KIND = "harbor-transfer-portal-physical-handoff"
_HANDOFF_SCHEMA = "1.0"


@dataclass(slots=True)
class MediaHandoffError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


HandoffFileRole = Literal[
    "bundle",
    "bundle-sidecar",
    "source-trust-package",
    "pending-trust-package",
]


class HandoffFile(BaseModel):
    role: HandoffFileRole
    name: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._-]+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class HandoffPayload(BaseModel):
    kind: Literal["harbor-transfer-portal-physical-handoff"] = _HANDOFF_KIND
    schema_version: Literal["1.0"] = _HANDOFF_SCHEMA
    delivery_id: str = Field(pattern=r"^DELIVERY-[0-9]{8}-[A-Z0-9]{6,32}$")
    created_at: str
    created_by: str = Field(min_length=1, max_length=128)
    signing_key_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    files: list[HandoffFile] = Field(min_length=2, max_length=4)

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: str) -> str:
        if not value.endswith("Z"):
            raise ValueError("created_at must be canonical UTC")
        return value

    @model_validator(mode="after")
    def require_exact_file_roles(self) -> HandoffPayload:
        roles = [item.role for item in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("handoff file roles must be unique")
        by_role = {item.role: item for item in self.files}
        if "bundle" not in by_role or "bundle-sidecar" not in by_role:
            raise ValueError("handoff must contain bundle and bundle-sidecar")
        expected_bundle = f"{self.delivery_id}.htp.tar.gz"
        if by_role["bundle"].name != expected_bundle:
            raise ValueError("bundle name does not match delivery_id")
        if by_role["bundle-sidecar"].name != f"{expected_bundle}.sha256":
            raise ValueError("sidecar name does not match delivery_id")
        for role in ("source-trust-package", "pending-trust-package"):
            item = by_role.get(role)
            if item is not None and not item.name.endswith(".htp-trust.tar.gz"):
                raise ValueError(f"{role} must use .htp-trust.tar.gz")
        return self


class SignedMediaHandoff(BaseModel):
    payload: HandoffPayload
    signature: str


@dataclass(frozen=True, slots=True)
class HandoffBuildResult:
    payload: bytes
    filename: str
    sha256: str
    signing_key_fingerprint: str


@dataclass(frozen=True, slots=True)
class HandoffVerificationResult:
    delivery_id: str
    signing_key_fingerprint: str
    bundle_sha256: str
    bundle_size_bytes: int
    created_at: str
    created_by: str


class MediaHandoffService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(
        self,
        *,
        manifest: BundleManifest,
        bundle_path: Path,
        sidecar_path: Path,
        private_key: Ed25519PrivateKey,
        optional_files: Sequence[tuple[HandoffFileRole, Path]] = (),
    ) -> HandoffBuildResult:
        if self.settings.portal_contour is not PortalContour.SOURCE:
            raise MediaHandoffError(
                "handoff_wrong_contour",
                "Physical handoff можно подписывать только в SOURCE",
            )
        fingerprint = ed25519_public_key_fingerprint(private_key.public_key())
        payload = HandoffPayload(
            delivery_id=manifest.delivery_id,
            created_at=manifest.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            created_by=manifest.created_by,
            signing_key_fingerprint=fingerprint,
            files=[
                self._file("bundle", bundle_path),
                self._file("bundle-sidecar", sidecar_path),
                *[
                    self._file(role, path)
                    for role, path in optional_files
                ],
            ],
        )
        payload_bytes = self._canonical_payload(payload)
        envelope = SignedMediaHandoff(
            payload=payload,
            signature=base64.b64encode(private_key.sign(payload_bytes)).decode("ascii"),
        )
        raw = self._canonical_envelope(envelope)
        return HandoffBuildResult(
            payload=raw,
            filename=f"{manifest.delivery_id}.htp-handoff.json",
            sha256=hashlib.sha256(raw).hexdigest(),
            signing_key_fingerprint=fingerprint,
        )

    def verify_from_discovery(self, raw: bytes) -> HandoffVerificationResult:
        if self.settings.portal_contour is not PortalContour.TARGET:
            raise MediaHandoffError(
                "handoff_wrong_contour",
                "Physical handoff можно проверять только в TARGET",
            )
        if not raw or len(raw) > self.settings.bundle_max_metadata_bytes:
            raise MediaHandoffError(
                "handoff_size_invalid",
                "Handoff manifest пуст или превышает metadata limit",
            )
        envelope = self._parse(raw)
        payload = envelope.payload
        expected_raw = self._canonical_envelope(envelope)
        if raw != expected_raw:
            raise MediaHandoffError(
                "handoff_not_canonical",
                "Handoff manifest должен быть canonical JSON",
            )
        payload_bytes = self._canonical_payload(payload)
        try:
            signature = base64.b64decode(envelope.signature, validate=True)
        except ValueError as exc:
            raise MediaHandoffError(
                "handoff_signature_invalid",
                "Handoff signature имеет неверный encoding",
            ) from exc
        if len(signature) != 64:
            raise MediaHandoffError(
                "handoff_signature_invalid",
                "Handoff signature имеет неверный размер",
            )
        try:
            trusted = KeyManagementService(self.settings).trusted_public_key(
                payload.signing_key_fingerprint,
                require_enabled=True,
            )
            trusted.verify(signature, payload_bytes)
        except KeyManagementError as exc:
            raise MediaHandoffError(
                "handoff_signer_untrusted",
                "Handoff подписан неизвестным или отключённым SOURCE key",
            ) from exc
        except InvalidSignature as exc:
            raise MediaHandoffError(
                "handoff_signature_invalid",
                "Handoff signature не прошла проверку",
            ) from exc

        root = self.settings.import_discovery_root.resolve()
        by_role = {item.role: item for item in payload.files}
        self._safe_discovery_file(root, by_role["bundle"])
        for expected in payload.files:
            if expected.role == "bundle":
                continue
            self._safe_discovery_file(root, expected)
        return HandoffVerificationResult(
            delivery_id=payload.delivery_id,
            signing_key_fingerprint=payload.signing_key_fingerprint,
            bundle_sha256=by_role["bundle"].sha256,
            bundle_size_bytes=by_role["bundle"].size_bytes,
            created_at=payload.created_at,
            created_by=payload.created_by,
        )

    def _safe_discovery_file(self, root: Path, expected: HandoffFile) -> Path:
        candidate = (root / expected.name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MediaHandoffError(
                "handoff_file_invalid",
                "Handoff file выходит за пределы incoming directory",
            ) from exc
        if not candidate.is_file() or candidate.is_symlink():
            raise MediaHandoffError(
                "handoff_file_missing",
                f"Файл physical handoff отсутствует: {expected.name}",
            )
        size = candidate.stat().st_size
        digest = self._sha256(candidate)
        if size != expected.size_bytes or digest != expected.sha256:
            raise MediaHandoffError(
                "handoff_file_mismatch",
                f"Файл physical handoff не совпадает с подписанным manifest: {expected.name}",
            )
        return candidate

    @staticmethod
    def _file(role: HandoffFileRole, path: Path) -> HandoffFile:
        if not path.is_file() or path.is_symlink():
            raise MediaHandoffError(
                "handoff_file_missing",
                f"Не найден published handoff file: {path.name}",
            )
        return HandoffFile(
            role=role,
            name=path.name,
            sha256=MediaHandoffService._sha256(path),
            size_bytes=path.stat().st_size,
        )

    @staticmethod
    def _parse(raw: bytes) -> SignedMediaHandoff:
        try:
            data = json.loads(raw.decode("utf-8"))
            return SignedMediaHandoff.model_validate(data)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise MediaHandoffError(
                "handoff_invalid",
                "Handoff manifest не прошёл validation",
            ) from exc

    @staticmethod
    def _canonical_payload(payload: HandoffPayload) -> bytes:
        return (
            json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _canonical_envelope(envelope: SignedMediaHandoff) -> bytes:
        return (
            json.dumps(
                envelope.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
