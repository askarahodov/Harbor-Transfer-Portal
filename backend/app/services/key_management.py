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

_MAX_KEY_MATERIAL_BYTES = 16 * 1024
_KEY_ID_RE = re.compile(r"^[a-f0-9]{64}$")
_DISABLED_DIR = ".disabled"


@dataclass(slots=True)
class KeyManagementError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SigningIdentityStatus:
    configured: bool
    key_id: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedKeyStatus:
    key_id: str
    fingerprint: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class _TrustedKeyEntry:
    status: TrustedKeyStatus
    path: Path


def public_key_fingerprint(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fingerprint_key_id(fingerprint: str) -> str:
    prefix = "sha256:"
    if not fingerprint.startswith(prefix):
        raise KeyManagementError("key_fingerprint_invalid", "Fingerprint ключа некорректен")
    key_id = fingerprint[len(prefix) :]
    if _KEY_ID_RE.fullmatch(key_id) is None:
        raise KeyManagementError("key_fingerprint_invalid", "Fingerprint ключа некорректен")
    return key_id


class KeyManagementService:
    """Manage bundle signing/trust material without exposing private key contents."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def signing_status(self) -> SigningIdentityStatus:
        self._require_contour(PortalContour.SOURCE)
        path = self.settings.bundle_signing_private_key_file
        if not path.exists():
            return SigningIdentityStatus(configured=False)
        key = self._load_private_key_file(path)
        fingerprint = public_key_fingerprint(key.public_key())
        return SigningIdentityStatus(
            configured=True,
            key_id=fingerprint_key_id(fingerprint),
            fingerprint=fingerprint,
        )

    def install_signing_private_key(
        self,
        private_key_pem: str,
        *,
        confirm_rotation: bool,
    ) -> tuple[SigningIdentityStatus, SigningIdentityStatus]:
        self._require_contour(PortalContour.SOURCE)
        encoded = private_key_pem.encode("utf-8")
        self._check_material_size(encoded)
        key = self._load_private_key(encoded)
        canonical = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        target = self.settings.bundle_signing_private_key_file
        before = self.signing_status() if target.exists() else SigningIdentityStatus(False)
        if before.configured and not confirm_rotation:
            raise KeyManagementError(
                "signing_rotation_confirmation_required",
                "Ротация SOURCE signing key требует явного подтверждения",
            )
        self._atomic_write(target, canonical, mode=0o600, replace=True)
        after = self.signing_status()
        return before, after

    def list_trusted_keys(self) -> tuple[TrustedKeyStatus, ...]:
        self._require_contour(PortalContour.TARGET)
        entries = self._trusted_entries()
        return tuple(
            entry.status
            for entry in sorted(entries, key=lambda item: (item.status.key_id, not item.status.enabled))
        )

    def add_trusted_key(self, public_key_pem: str) -> TrustedKeyStatus:
        self._require_contour(PortalContour.TARGET)
        key = self._load_public_key(public_key_pem.encode("utf-8"))
        fingerprint = public_key_fingerprint(key)
        key_id = fingerprint_key_id(fingerprint)
        entries = self._trusted_entries()
        if any(entry.status.key_id == key_id for entry in entries):
            raise KeyManagementError(
                "trusted_key_exists",
                "Trusted SOURCE public key уже зарегистрирован",
            )
        active_count = sum(entry.status.enabled for entry in entries)
        if active_count >= self.settings.bundle_max_trusted_keys:
            raise KeyManagementError(
                "trusted_key_limit_exceeded",
                "Достигнут лимит active trusted SOURCE public keys",
            )
        directory = self._ensure_trust_directory()
        target = directory / f"{key_id}.pem"
        canonical = self._serialize_public_key(key)
        self._atomic_write(target, canonical, mode=0o644, replace=False)
        return TrustedKeyStatus(key_id=key_id, fingerprint=fingerprint, enabled=True)

    def replace_trusted_key(
        self,
        key_id: str,
        public_key_pem: str,
        *,
        confirm: bool,
    ) -> tuple[TrustedKeyStatus, TrustedKeyStatus]:
        self._require_contour(PortalContour.TARGET)
        if not confirm:
            raise KeyManagementError(
                "trusted_key_confirmation_required",
                "Замена trusted SOURCE public key требует явного подтверждения",
            )
        entry = self._find_trusted_entry(key_id, enabled=True)
        key = self._load_public_key(public_key_pem.encode("utf-8"))
        fingerprint = public_key_fingerprint(key)
        new_key_id = fingerprint_key_id(fingerprint)
        entries = self._trusted_entries()
        if new_key_id != key_id and any(item.status.key_id == new_key_id for item in entries):
            raise KeyManagementError(
                "trusted_key_exists",
                "Новый trusted SOURCE public key уже зарегистрирован",
            )
        before = entry.status
        self._atomic_write(
            entry.path,
            self._serialize_public_key(key),
            mode=0o644,
            replace=True,
        )
        after = TrustedKeyStatus(
            key_id=new_key_id,
            fingerprint=fingerprint,
            enabled=True,
        )
        return before, after

    def set_trusted_key_enabled(
        self,
        key_id: str,
        *,
        enabled: bool,
        confirm: bool,
    ) -> TrustedKeyStatus:
        self._require_contour(PortalContour.TARGET)
        if not confirm:
            raise KeyManagementError(
                "trusted_key_confirmation_required",
                "Изменение состояния trusted SOURCE public key требует явного подтверждения",
            )
        entry = self._find_trusted_entry(key_id, enabled=not enabled)
        directory = self._ensure_trust_directory()
        disabled_dir = self._ensure_disabled_directory(directory)
        destination = (
            directory / f"{key_id}.pem"
            if enabled
            else disabled_dir / f"{key_id}.pem"
        )
        if destination.exists() or destination.is_symlink():
            raise KeyManagementError(
                "trusted_key_exists",
                "Trusted SOURCE public key уже существует в целевом состоянии",
            )
        os.replace(entry.path, destination)
        os.chmod(destination, 0o644)
        return TrustedKeyStatus(
            key_id=entry.status.key_id,
            fingerprint=entry.status.fingerprint,
            enabled=enabled,
        )

    def remove_trusted_key(self, key_id: str, *, confirm: bool) -> TrustedKeyStatus:
        self._require_contour(PortalContour.TARGET)
        if not confirm:
            raise KeyManagementError(
                "trusted_key_confirmation_required",
                "Удаление trusted SOURCE public key требует явного подтверждения",
            )
        entry = self._find_trusted_entry(key_id)
        entry.path.unlink()
        return entry.status

    def _trusted_entries(self) -> tuple[_TrustedKeyEntry, ...]:
        configured = self.settings.bundle_trusted_public_keys_dir
        if configured.exists() and configured.is_symlink():
            raise KeyManagementError(
                "trusted_key_directory_invalid",
                "Каталог trusted SOURCE public keys не может быть symlink",
            )
        directory = configured.resolve()
        if not directory.exists():
            return ()
        if not directory.is_dir():
            raise KeyManagementError(
                "trusted_key_directory_invalid",
                "Путь trusted SOURCE public keys должен быть каталогом",
            )

        candidates: list[tuple[Path, bool]] = [
            (path, True) for path in sorted(directory.glob("*.pem"))
        ]
        disabled_dir = directory / _DISABLED_DIR
        if disabled_dir.exists():
            if not disabled_dir.is_dir() or disabled_dir.is_symlink():
                raise KeyManagementError(
                    "trusted_key_directory_invalid",
                    "Каталог disabled trusted keys некорректен",
                )
            candidates.extend((path, False) for path in sorted(disabled_dir.glob("*.pem")))

        entries: list[_TrustedKeyEntry] = []
        seen: set[str] = set()
        for path, enabled in candidates:
            key = self._load_public_key_file(path)
            fingerprint = public_key_fingerprint(key)
            key_id = fingerprint_key_id(fingerprint)
            if key_id in seen:
                raise KeyManagementError(
                    "trusted_key_duplicate",
                    "Один trusted SOURCE public key зарегистрирован более одного раза",
                )
            seen.add(key_id)
            entries.append(
                _TrustedKeyEntry(
                    status=TrustedKeyStatus(
                        key_id=key_id,
                        fingerprint=fingerprint,
                        enabled=enabled,
                    ),
                    path=path,
                )
            )
        if sum(entry.status.enabled for entry in entries) > self.settings.bundle_max_trusted_keys:
            raise KeyManagementError(
                "trusted_key_limit_exceeded",
                "Количество active trusted SOURCE public keys превышает configured limit",
            )
        return tuple(entries)

    def _find_trusted_entry(
        self,
        key_id: str,
        *,
        enabled: bool | None = None,
    ) -> _TrustedKeyEntry:
        self._validate_key_id(key_id)
        for entry in self._trusted_entries():
            if entry.status.key_id == key_id and (
                enabled is None or entry.status.enabled is enabled
            ):
                return entry
        raise KeyManagementError(
            "trusted_key_not_found",
            "Trusted SOURCE public key не найден",
        )

    def _ensure_trust_directory(self) -> Path:
        configured = self.settings.bundle_trusted_public_keys_dir
        if configured.exists() and configured.is_symlink():
            raise KeyManagementError(
                "trusted_key_directory_invalid",
                "Каталог trusted SOURCE public keys не может быть symlink",
            )
        directory = configured.resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        return directory

    @staticmethod
    def _ensure_disabled_directory(directory: Path) -> Path:
        disabled = directory / _DISABLED_DIR
        if disabled.exists() and disabled.is_symlink():
            raise KeyManagementError(
                "trusted_key_directory_invalid",
                "Каталог disabled trusted keys не может быть symlink",
            )
        disabled.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(disabled, 0o700)
        return disabled

    def _load_private_key_file(self, path: Path) -> Ed25519PrivateKey:
        self._validate_regular_file(path, private=True)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise KeyManagementError(
                "signing_key_invalid",
                "SOURCE signing private key не читается",
            ) from exc
        self._check_material_size(payload)
        return self._load_private_key(payload)

    def _load_public_key_file(self, path: Path) -> Ed25519PublicKey:
        self._validate_regular_file(path, private=False)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise KeyManagementError(
                "trusted_key_invalid",
                "Trusted SOURCE public key не читается",
            ) from exc
        return self._load_public_key(payload)

    def _load_private_key(self, payload: bytes) -> Ed25519PrivateKey:
        self._check_material_size(payload)
        try:
            key = serialization.load_pem_private_key(payload, password=None)
        except (TypeError, ValueError) as exc:
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

    def _load_public_key(self, payload: bytes) -> Ed25519PublicKey:
        self._check_material_size(payload)
        try:
            key = serialization.load_pem_public_key(payload)
        except (TypeError, ValueError) as exc:
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

    @staticmethod
    def _serialize_public_key(key: Ed25519PublicKey) -> bytes:
        return key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @staticmethod
    def _validate_regular_file(path: Path, *, private: bool) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            code = "signing_key_not_configured" if private else "trusted_key_invalid"
            raise KeyManagementError(code, "Key file недоступен") from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise KeyManagementError(
                "signing_key_invalid" if private else "trusted_key_invalid",
                "Key material должен храниться в обычном файле",
            )
        if private and stat.S_IMODE(info.st_mode) & 0o077:
            raise KeyManagementError(
                "signing_key_permissions",
                "SOURCE signing private key должен быть недоступен group/other",
            )

    @staticmethod
    def _check_material_size(payload: bytes) -> None:
        if not payload or len(payload) > _MAX_KEY_MATERIAL_BYTES:
            raise KeyManagementError(
                "key_material_size_invalid",
                "Размер key material вне допустимого диапазона",
            )

    @staticmethod
    def _validate_key_id(key_id: str) -> None:
        if _KEY_ID_RE.fullmatch(key_id) is None:
            raise KeyManagementError("key_id_invalid", "Key id некорректен")

    def _require_contour(self, contour: PortalContour) -> None:
        if self.settings.portal_contour is not contour:
            raise KeyManagementError(
                "key_management_wrong_contour",
                f"Операция доступна только в контуре {contour.value}",
            )

    @staticmethod
    def _atomic_write(path: Path, payload: bytes, *, mode: int, replace: bool) -> None:
        parent = path.parent
        if parent.exists() and parent.is_symlink():
            raise KeyManagementError(
                "key_storage_invalid",
                "Каталог key storage не может быть symlink",
            )
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, 0o700)
        if path.exists() and path.is_symlink():
            raise KeyManagementError(
                "key_storage_invalid",
                "Key file не может быть symlink",
            )
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, mode)
            if replace:
                os.replace(temp_path, path)
            else:
                try:
                    os.link(temp_path, path)
                except FileExistsError as exc:
                    raise KeyManagementError(
                        "trusted_key_exists",
                        "Trusted SOURCE public key уже зарегистрирован",
                    ) from exc
                temp_path.unlink()
        finally:
            temp_path.unlink(missing_ok=True)
