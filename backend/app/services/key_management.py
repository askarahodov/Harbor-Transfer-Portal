from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import PortalContour, Settings

_MAX_KEY_BYTES = 64 * 1024
_FINGERPRINT_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(slots=True)
class KeyManagementError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SourceSigningKeyStatus:
    configured: bool
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedPublicKeyStatus:
    fingerprint: str
    enabled: bool


def public_key_fingerprint(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class KeyManagementService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.private_key_path = settings.bundle_signing_private_key_file.resolve()
        self.trust_dir = settings.bundle_trusted_public_keys_dir.resolve()

    def source_status(self) -> SourceSigningKeyStatus:
        self._require_contour(PortalContour.SOURCE)
        if not self.private_key_path.exists():
            return SourceSigningKeyStatus(configured=False)
        key = self._load_private_key_file(self.private_key_path)
        return SourceSigningKeyStatus(
            configured=True,
            fingerprint=public_key_fingerprint(key.public_key()),
        )

    def install_source_private_key(self, pem: str) -> SourceSigningKeyStatus:
        self._require_contour(PortalContour.SOURCE)
        data = self._bounded_text(pem)
        key = self._parse_private_key(data)
        canonical = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._atomic_write(self.private_key_path, canonical)
        return SourceSigningKeyStatus(
            configured=True,
            fingerprint=public_key_fingerprint(key.public_key()),
        )

    def list_trusted_keys(self) -> tuple[TrustedPublicKeyStatus, ...]:
        self._require_contour(PortalContour.TARGET)
        if not self.trust_dir.exists():
            return ()
        if not self.trust_dir.is_dir() or self.trust_dir.is_symlink():
            raise KeyManagementError(
                "key_trust_directory_invalid",
                "Каталог trusted SOURCE keys имеет небезопасный тип",
            )
        records: dict[str, bool] = {}
        for path in sorted(self.trust_dir.iterdir(), key=lambda item: item.name):
            if not (path.name.endswith(".pem") or path.name.endswith(".pem.disabled")):
                continue
            key = self._load_public_key_file(path)
            fingerprint = public_key_fingerprint(key)
            enabled = path.name.endswith(".pem")
            records[fingerprint] = records.get(fingerprint, False) or enabled
        return tuple(
            TrustedPublicKeyStatus(fingerprint=fingerprint, enabled=enabled)
            for fingerprint, enabled in sorted(records.items())
        )

    def add_trusted_key(self, pem: str) -> TrustedPublicKeyStatus:
        self._require_contour(PortalContour.TARGET)
        return self._install_trusted_key(pem, capacity_credit=0)

    def set_trusted_key_enabled(
        self,
        fingerprint: str,
        *,
        enabled: bool,
    ) -> TrustedPublicKeyStatus:
        self._require_contour(PortalContour.TARGET)
        normalized = self._normalize_fingerprint(fingerprint)
        paths = self._paths_for_fingerprint(normalized)
        if not paths:
            raise KeyManagementError("key_trusted_not_found", "Trusted public key не найден")
        current_enabled = [path for path in paths if path.name.endswith(".pem")]
        if enabled and current_enabled:
            return TrustedPublicKeyStatus(fingerprint=normalized, enabled=True)
        if not enabled and not current_enabled:
            return TrustedPublicKeyStatus(fingerprint=normalized, enabled=False)
        if enabled:
            self._ensure_enabled_capacity()
            source = paths[0]
            target = self._enabled_path(normalized)
        else:
            source = current_enabled[0]
            target = self._disabled_path(normalized)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        if target.exists() and target != source:
            target.unlink()
        os.replace(source, target)
        os.chmod(target, 0o600)
        for path in paths:
            if path != target:
                path.unlink(missing_ok=True)
        self._fsync_directory(target.parent)
        return TrustedPublicKeyStatus(fingerprint=normalized, enabled=enabled)

    def remove_trusted_key(self, fingerprint: str) -> None:
        self._require_contour(PortalContour.TARGET)
        normalized = self._normalize_fingerprint(fingerprint)
        paths = self._paths_for_fingerprint(normalized)
        if not paths:
            raise KeyManagementError("key_trusted_not_found", "Trusted public key не найден")
        for path in paths:
            path.unlink(missing_ok=True)
        if self.trust_dir.exists():
            self._fsync_directory(self.trust_dir)

    def replace_trusted_key(
        self,
        fingerprint: str,
        pem: str,
    ) -> TrustedPublicKeyStatus:
        self._require_contour(PortalContour.TARGET)
        old = self._normalize_fingerprint(fingerprint)
        old_paths = self._paths_for_fingerprint(old)
        if not old_paths:
            raise KeyManagementError("key_trusted_not_found", "Trusted public key не найден")
        old_enabled = any(path.name.endswith(".pem") for path in old_paths)
        new_key = self._install_trusted_key(
            pem,
            capacity_credit=1 if old_enabled else 0,
        )
        if new_key.fingerprint != old:
            self.remove_trusted_key(old)
        return new_key

    def _install_trusted_key(
        self,
        pem: str,
        *,
        capacity_credit: int,
    ) -> TrustedPublicKeyStatus:
        key = self._parse_public_key(self._bounded_text(pem))
        fingerprint = public_key_fingerprint(key)
        existing = self._paths_for_fingerprint(fingerprint)
        if any(path.name.endswith(".pem") for path in existing):
            return TrustedPublicKeyStatus(fingerprint=fingerprint, enabled=True)
        self._ensure_enabled_capacity(capacity_credit=capacity_credit)
        canonical = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        target = self._enabled_path(fingerprint)
        self._atomic_write(target, canonical)
        for path in existing:
            if path != target:
                path.unlink(missing_ok=True)
        return TrustedPublicKeyStatus(fingerprint=fingerprint, enabled=True)

    def _paths_for_fingerprint(self, fingerprint: str) -> tuple[Path, ...]:
        if not self.trust_dir.exists():
            return ()
        paths: list[Path] = []
        for path in sorted(self.trust_dir.iterdir(), key=lambda item: item.name):
            if not (path.name.endswith(".pem") or path.name.endswith(".pem.disabled")):
                continue
            key = self._load_public_key_file(path)
            if public_key_fingerprint(key) == fingerprint:
                paths.append(path)
        return tuple(paths)

    def _ensure_enabled_capacity(self, *, capacity_credit: int = 0) -> None:
        enabled = sum(1 for item in self.list_trusted_keys() if item.enabled)
        if enabled - capacity_credit >= self.settings.bundle_max_trusted_keys:
            raise KeyManagementError(
                "key_trust_limit_exceeded",
                "Достигнут configured limit trusted SOURCE public keys",
            )

    def _enabled_path(self, fingerprint: str) -> Path:
        return self.trust_dir / f"ed25519-{fingerprint.removeprefix('sha256:')}.pem"

    def _disabled_path(self, fingerprint: str) -> Path:
        return self.trust_dir / (
            f"ed25519-{fingerprint.removeprefix('sha256:')}.pem.disabled"
        )

    def _load_private_key_file(self, path: Path) -> Ed25519PrivateKey:
        data = self._read_key_file(path, private=True)
        return self._parse_private_key(data)

    def _load_public_key_file(self, path: Path) -> Ed25519PublicKey:
        data = self._read_key_file(path, private=False)
        return self._parse_public_key(data)

    def _read_key_file(self, path: Path, *, private: bool) -> bytes:
        try:
            info = path.lstat()
        except OSError as exc:
            raise KeyManagementError("key_file_invalid", "Key file не читается") from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise KeyManagementError("key_file_invalid", "Key должен быть обычным файлом")
        if info.st_size <= 0 or info.st_size > _MAX_KEY_BYTES:
            raise KeyManagementError("key_file_invalid", "Размер key file недопустим")
        if private and stat.S_IMODE(info.st_mode) & 0o077:
            raise KeyManagementError(
                "key_private_permissions",
                "SOURCE private key должен иметь mode 0600 или строже",
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise KeyManagementError("key_file_invalid", "Key file не читается") from exc

    @staticmethod
    def _parse_private_key(data: bytes) -> Ed25519PrivateKey:
        try:
            key = serialization.load_pem_private_key(data, password=None)
        except (TypeError, ValueError) as exc:
            raise KeyManagementError(
                "key_private_invalid",
                "Ожидается незашифрованный PEM Ed25519 private key",
            ) from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise KeyManagementError(
                "key_private_invalid",
                "SOURCE signing key должен быть Ed25519 private key",
            )
        return key

    @staticmethod
    def _parse_public_key(data: bytes) -> Ed25519PublicKey:
        try:
            key = serialization.load_pem_public_key(data)
        except (TypeError, ValueError) as exc:
            raise KeyManagementError(
                "key_public_invalid",
                "Ожидается PEM Ed25519 public key; private key запрещён",
            ) from exc
        if not isinstance(key, Ed25519PublicKey):
            raise KeyManagementError(
                "key_public_invalid",
                "Trusted key должен быть Ed25519 public key",
            )
        return key

    @staticmethod
    def _bounded_text(value: str) -> bytes:
        data = value.encode("utf-8")
        if not data or len(data) > _MAX_KEY_BYTES:
            raise KeyManagementError("key_payload_invalid", "Размер key payload недопустим")
        return data

    @staticmethod
    def _normalize_fingerprint(value: str) -> str:
        normalized = value.strip().lower()
        if _FINGERPRINT_RE.fullmatch(normalized) is None:
            raise KeyManagementError("key_fingerprint_invalid", "Fingerprint имеет неверный формат")
        return normalized

    def _require_contour(self, expected: PortalContour) -> None:
        if self.settings.portal_contour is not expected:
            raise KeyManagementError(
                "key_wrong_contour",
                f"Операция управления ключами доступна только в контуре {expected.value}",
            )

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = -1
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            KeyManagementService._fsync_directory(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
