from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import PortalContour, Settings
from app.services.key_material import ed25519_public_key_fingerprint

_FINGERPRINT_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(slots=True)
class KeyManagementError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SigningKeyStatus:
    configured: bool
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class TrustedKeyStatus:
    fingerprint: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class KeyMutation:
    action: str
    fingerprint: str


class KeyManagementService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.signing_path = settings.bundle_signing_private_key_file.absolute()
        self.trusted_dir = settings.bundle_trusted_public_keys_dir.absolute()

    def signing_status(self) -> SigningKeyStatus:
        self._require_source()
        if not self.signing_path.exists() and not self.signing_path.is_symlink():
            return SigningKeyStatus(configured=False, fingerprint=None)
        key = self._read_private_key_file(self.signing_path)
        return SigningKeyStatus(
            configured=True,
            fingerprint=ed25519_public_key_fingerprint(key.public_key()),
        )

    def install_signing_private_key(self, pem: str) -> KeyMutation:
        self._require_source()
        key = self._parse_private_key(self._bounded_bytes(pem))
        fingerprint = ed25519_public_key_fingerprint(key.public_key())
        if self.signing_path.is_symlink():
            raise KeyManagementError(
                "signing_key_invalid",
                "SOURCE signing key path не может быть symlink",
            )
        action = "rotated" if self.signing_path.exists() else "installed"
        normalized = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._atomic_write(self.signing_path, normalized, 0o600)
        return KeyMutation(action=action, fingerprint=fingerprint)

    def list_trusted_keys(self) -> tuple[TrustedKeyStatus, ...]:
        self._require_target()
        if not self.trusted_dir.exists():
            return ()
        self._require_trusted_directory()

        states: dict[str, bool] = {}
        for path, enabled in self._trusted_key_files():
            key = self._read_public_key_file(path)
            fingerprint = ed25519_public_key_fingerprint(key)
            states[fingerprint] = states.get(fingerprint, False) or enabled
        return tuple(
            TrustedKeyStatus(fingerprint=fingerprint, enabled=enabled)
            for fingerprint, enabled in sorted(states.items())
        )

    def add_trusted_public_key(self, pem: str) -> KeyMutation:
        self._require_target()
        key = self._parse_public_key(self._bounded_bytes(pem))
        fingerprint = ed25519_public_key_fingerprint(key)
        existing = self._find_trusted_key_files(fingerprint)
        unique = self.list_trusted_keys()
        if not existing and len(unique) >= self.settings.bundle_max_trusted_keys:
            raise KeyManagementError(
                "trusted_key_limit_exceeded",
                "Достигнут лимит trusted SOURCE public keys",
            )

        normalized = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        target = self._enabled_path(fingerprint)
        self._atomic_write(target, normalized, 0o600)
        self._remove_other_matches(existing, keep=target)
        self._disabled_path(fingerprint).unlink(missing_ok=True)
        self._fsync_directory(self.trusted_dir)
        return KeyMutation(
            action="replaced" if existing else "added",
            fingerprint=fingerprint,
        )

    def replace_trusted_public_key(self, fingerprint: str, pem: str) -> KeyMutation:
        self._require_target()
        previous = self._validate_fingerprint(fingerprint)
        previous_matches = self._find_trusted_key_files(previous)
        if not previous_matches:
            raise KeyManagementError("trusted_key_not_found", "Trusted public key не найден")

        key = self._parse_public_key(self._bounded_bytes(pem))
        replacement = ed25519_public_key_fingerprint(key)
        replacement_matches = self._find_trusted_key_files(replacement)
        normalized = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        target = self._enabled_path(replacement)

        # Publish the replacement before removing the previous key. Replacement does not
        # increase the final unique-key count, so it remains valid even at the configured
        # trust-set capacity. A crash between these steps leaves a safe overlap state.
        self._atomic_write(target, normalized, 0o600)
        self._remove_other_matches(replacement_matches, keep=target)
        self._disabled_path(replacement).unlink(missing_ok=True)
        self._fsync_directory(self.trusted_dir)

        if replacement != previous:
            for path in previous_matches:
                if path != target:
                    path.unlink(missing_ok=True)
            self._enabled_path(previous).unlink(missing_ok=True)
            self._disabled_path(previous).unlink(missing_ok=True)
            self._fsync_directory(self.trusted_dir)

        return KeyMutation(action="replaced", fingerprint=replacement)

    def set_trusted_key_enabled(self, fingerprint: str, enabled: bool) -> KeyMutation:
        self._require_target()
        normalized = self._validate_fingerprint(fingerprint)
        matches = self._find_trusted_key_files(normalized)
        if not matches:
            raise KeyManagementError("trusted_key_not_found", "Trusted public key не найден")

        source = matches[0]
        key = self._read_public_key_file(source)
        expected = ed25519_public_key_fingerprint(key)
        if expected != normalized:
            raise KeyManagementError(
                "trusted_key_store_invalid",
                "Trusted key store содержит inconsistent fingerprint",
            )
        payload = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        target = self._enabled_path(normalized) if enabled else self._disabled_path(normalized)
        self._atomic_write(target, payload, 0o600)
        self._remove_other_matches(matches, keep=target)
        opposite = self._disabled_path(normalized) if enabled else self._enabled_path(normalized)
        opposite.unlink(missing_ok=True)
        self._fsync_directory(self.trusted_dir)
        return KeyMutation(
            action="enabled" if enabled else "disabled",
            fingerprint=normalized,
        )

    def remove_trusted_key(self, fingerprint: str) -> KeyMutation:
        self._require_target()
        normalized = self._validate_fingerprint(fingerprint)
        matches = self._find_trusted_key_files(normalized)
        if not matches:
            raise KeyManagementError("trusted_key_not_found", "Trusted public key не найден")
        for path in matches:
            path.unlink(missing_ok=True)
        self._enabled_path(normalized).unlink(missing_ok=True)
        self._disabled_path(normalized).unlink(missing_ok=True)
        self._fsync_directory(self.trusted_dir)
        return KeyMutation(action="removed", fingerprint=normalized)

    def _require_source(self) -> None:
        if self.settings.portal_contour is not PortalContour.SOURCE:
            raise KeyManagementError(
                "key_management_wrong_contour",
                "SOURCE signing identity можно изменять только в контуре SOURCE",
            )

    def _require_target(self) -> None:
        if self.settings.portal_contour is not PortalContour.TARGET:
            raise KeyManagementError(
                "key_management_wrong_contour",
                "Trusted SOURCE public keys можно изменять только в контуре TARGET",
            )

    def _bounded_bytes(self, pem: str) -> bytes:
        data = pem.encode("utf-8")
        if not data or len(data) > self.settings.bundle_key_material_max_bytes:
            raise KeyManagementError(
                "key_material_size_invalid",
                "Key material пуст или превышает допустимый размер",
            )
        return data

    @staticmethod
    def _parse_private_key(data: bytes) -> Ed25519PrivateKey:
        try:
            key = serialization.load_pem_private_key(data, password=None)
        except (ValueError, TypeError) as exc:
            raise KeyManagementError(
                "signing_key_invalid",
                "Ожидается незашифрованный PEM Ed25519 private key",
            ) from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise KeyManagementError(
                "signing_key_invalid",
                "SOURCE signing key должен быть Ed25519 private key",
            )
        return key

    @staticmethod
    def _parse_public_key(data: bytes) -> Ed25519PublicKey:
        try:
            key = serialization.load_pem_public_key(data)
        except (ValueError, TypeError) as exc:
            raise KeyManagementError(
                "trusted_key_invalid",
                "Ожидается PEM Ed25519 public key",
            ) from exc
        if not isinstance(key, Ed25519PublicKey):
            raise KeyManagementError(
                "trusted_key_invalid",
                "Trusted SOURCE key должен быть Ed25519 public key",
            )
        return key

    def _read_private_key_file(self, path: Path) -> Ed25519PrivateKey:
        try:
            info = path.lstat()
        except OSError as exc:
            raise KeyManagementError(
                "signing_key_unavailable",
                "SOURCE signing private key недоступен",
            ) from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise KeyManagementError(
                "signing_key_invalid",
                "SOURCE signing key должен быть обычным файлом",
            )
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise KeyManagementError(
                "signing_key_permissions",
                "SOURCE signing private key должен быть недоступен group/other",
            )
        if info.st_size > self.settings.bundle_key_material_max_bytes:
            raise KeyManagementError(
                "key_material_size_invalid",
                "SOURCE signing private key превышает допустимый размер",
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise KeyManagementError(
                "signing_key_unavailable",
                "SOURCE signing private key недоступен",
            ) from exc
        return self._parse_private_key(data)

    def _read_public_key_file(self, path: Path) -> Ed25519PublicKey:
        try:
            info = path.lstat()
        except OSError as exc:
            raise KeyManagementError(
                "trusted_key_store_invalid",
                "Trusted public key недоступен",
            ) from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise KeyManagementError(
                "trusted_key_store_invalid",
                "Symlink/non-file trusted key запрещён",
            )
        if info.st_size > self.settings.bundle_key_material_max_bytes:
            raise KeyManagementError(
                "key_material_size_invalid",
                "Trusted public key превышает допустимый размер",
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise KeyManagementError(
                "trusted_key_store_invalid",
                "Trusted public key недоступен",
            ) from exc
        return self._parse_public_key(data)

    def _require_trusted_directory(self) -> None:
        if not self.trusted_dir.is_dir() or self.trusted_dir.is_symlink():
            raise KeyManagementError(
                "trusted_key_store_invalid",
                "Каталог trusted SOURCE keys некорректен",
            )

    def _trusted_key_files(self) -> tuple[tuple[Path, bool], ...]:
        if not self.trusted_dir.exists():
            return ()
        self._require_trusted_directory()
        active = [(path, True) for path in sorted(self.trusted_dir.glob("*.pem"))]
        disabled = [(path, False) for path in sorted(self.trusted_dir.glob("*.disabled"))]
        return tuple(active + disabled)

    def _find_trusted_key_files(self, fingerprint: str) -> list[Path]:
        if not self.trusted_dir.exists():
            return []
        result: list[Path] = []
        for path, _enabled in self._trusted_key_files():
            try:
                key = self._read_public_key_file(path)
            except KeyManagementError:
                continue
            if ed25519_public_key_fingerprint(key) == fingerprint:
                result.append(path)
        return result

    @staticmethod
    def _validate_fingerprint(fingerprint: str) -> str:
        normalized = fingerprint.strip().lower()
        if _FINGERPRINT_RE.fullmatch(normalized) is None:
            raise KeyManagementError(
                "trusted_key_fingerprint_invalid",
                "Trusted key fingerprint имеет неверный формат",
            )
        return normalized

    def _enabled_path(self, fingerprint: str) -> Path:
        return self.trusted_dir / f"{fingerprint.removeprefix('sha256:')}.pem"

    def _disabled_path(self, fingerprint: str) -> Path:
        return self.trusted_dir / f"{fingerprint.removeprefix('sha256:')}.disabled"

    @staticmethod
    def _remove_other_matches(paths: list[Path], *, keep: Path) -> None:
        for path in paths:
            if path != keep:
                path.unlink(missing_ok=True)

    def _atomic_write(self, path: Path, payload: bytes, mode: int) -> None:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise KeyManagementError(
                "key_store_invalid",
                "Каталог key material некорректен",
            )
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except OSError as exc:
            raise KeyManagementError(
                "key_store_write_failed",
                "Не удалось атомарно сохранить key material",
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

        # Rename is the commit boundary: the replacement is already authoritative here.
        # Directory fsync improves crash durability, but it must not turn a committed key
        # replacement into a false API failure that claims the old key is still active.
        try:
            self._fsync_directory(parent)
        except OSError:
            pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
