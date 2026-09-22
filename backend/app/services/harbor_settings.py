from __future__ import annotations

import os
import re
import shutil
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import AnyHttpUrl, TypeAdapter
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, validate_harbor_base_url
from app.db.models import HarborProfile, Operation
from app.db.repositories import HarborProfileRepository, SettingMetadataRepository
from app.domain.operations import TERMINAL_STATES
from app.services.harbor_client import HarborClient

HARBOR_URL_KEY = "harbor.url"
HARBOR_USERNAME_KEY = "harbor.username"
HARBOR_VERIFY_TLS_KEY = "harbor.verify_tls"
DEFAULT_HARBOR_PROFILE_ID = "default"
_HARBOR_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_PROFILE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class EffectiveHarborSettings:
    url: str | None
    username: str | None
    password: str | None
    verify_tls: bool
    ca_file: Path | None


@dataclass(frozen=True, slots=True)
class HarborProfileSnapshot:
    id: str
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class HarborProfileInfo:
    id: str
    name: str
    url: str | None
    username: str | None
    verify_tls: bool
    enabled: bool
    credential_configured: bool
    custom_ca_configured: bool
    legacy_default: bool = False


@dataclass(slots=True)
class HarborSettingsError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class HarborSettingsService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.metadata = SettingMetadataRepository(session)
        self.profiles = HarborProfileRepository(session)

    def resolve(self, profile_id: str | None = None) -> EffectiveHarborSettings:
        normalized = self._normalized_profile_id(profile_id)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            return self._resolve_default()

        profile = self._required_profile(normalized)
        if not profile.enabled:
            raise HarborSettingsError(
                "harbor_profile_disabled",
                "Выбранный Harbor profile отключён",
            )
        ca_file = self._profile_ca_path(normalized)
        effective_ca = ca_file if ca_file.is_file() else self.settings.harbor_ca_file
        return EffectiveHarborSettings(
            url=self._validated_url(profile.url),
            username=profile.username or None,
            password=self._read_profile_password(normalized),
            verify_tls=profile.verify_tls,
            ca_file=effective_ca if profile.verify_tls else None,
        )

    def build_client(self, profile_id: str | None = None) -> HarborClient:
        resolved = self.resolve(profile_id)
        if not resolved.url:
            raise HarborSettingsError("harbor_not_configured", "Локальный Harbor не настроен")
        if resolved.ca_file is not None and not resolved.ca_file.is_file():
            raise HarborSettingsError(
                "harbor_ca_unavailable",
                "Настроенный CA-файл локального Harbor недоступен",
            )

        verify: bool | str = resolved.verify_tls
        if resolved.verify_tls and resolved.ca_file is not None:
            verify = str(resolved.ca_file)
        return HarborClient(
            base_url=resolved.url,
            username=resolved.username,
            password=resolved.password,
            verify=verify,
            connect_timeout=self.settings.harbor_connect_timeout_seconds,
            read_timeout=self.settings.harbor_read_timeout_seconds,
        )

    def profile_snapshot(self, profile_id: str | None = None) -> HarborProfileSnapshot:
        normalized = self._normalized_profile_id(profile_id)
        resolved = self.resolve(normalized)
        if not resolved.url:
            raise HarborSettingsError("harbor_not_configured", "Локальный Harbor не настроен")
        name = (
            "Default"
            if normalized == DEFAULT_HARBOR_PROFILE_ID
            else self._required_profile(normalized).name
        )
        return HarborProfileSnapshot(id=normalized, name=name, url=resolved.url)

    def list_profiles(self) -> list[HarborProfileInfo]:
        default = self._resolve_default()
        items = [
            HarborProfileInfo(
                id=DEFAULT_HARBOR_PROFILE_ID,
                name="Default",
                url=default.url,
                username=default.username,
                verify_tls=default.verify_tls,
                enabled=True,
                credential_configured=self.credential_configured(DEFAULT_HARBOR_PROFILE_ID),
                custom_ca_configured=self.custom_ca_configured(DEFAULT_HARBOR_PROFILE_ID),
                legacy_default=True,
            )
        ]
        for profile in self.profiles.list():
            items.append(self._profile_info(profile))
        return items

    def get_profile(self, profile_id: str) -> HarborProfileInfo:
        normalized = self._normalized_profile_id(profile_id)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            return self.list_profiles()[0]
        return self._profile_info(self._required_profile(normalized))

    def create_profile(
        self,
        *,
        name: str,
        url: str,
        username: str | None,
        verify_tls: bool,
    ) -> HarborProfileInfo:
        normalized_name = self._validated_profile_name(name)
        self._assert_unique_name(normalized_name)
        validated_url = self._validated_url(url)
        if validated_url is None:
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "URL Harbor profile обязателен",
            )
        profile = self.profiles.create(
            profile_id=uuid4().hex,
            name=normalized_name,
            url=validated_url,
            username=self._normalized_username(username),
            verify_tls=verify_tls,
        )
        return self._profile_info(profile)

    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | object = _MISSING,
        url: str | object = _MISSING,
        username: str | None | object = _MISSING,
        verify_tls: bool | object = _MISSING,
        enabled: bool | object = _MISSING,
    ) -> HarborProfileInfo:
        normalized = self._normalized_profile_id(profile_id)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            raise HarborSettingsError(
                "harbor_profile_default_managed_separately",
                "Default profile редактируется через текущие настройки локального Harbor",
            )
        self._assert_profile_mutable(normalized)
        profile = self._required_profile(normalized)

        if name is not _MISSING:
            normalized_name = self._validated_profile_name(str(name))
            if normalized_name.casefold() != profile.name.casefold():
                self._assert_unique_name(normalized_name, exclude_profile_id=normalized)
            profile.name = normalized_name
        if url is not _MISSING:
            validated_url = self._validated_url(str(url))
            if validated_url is None:
                raise HarborSettingsError(
                    "harbor_configuration_invalid",
                    "URL Harbor profile обязателен",
                )
            profile.url = validated_url
        if username is not _MISSING:
            profile.username = self._normalized_username(
                username if isinstance(username, str) or username is None else str(username)
            )
        if verify_tls is not _MISSING:
            profile.verify_tls = bool(verify_tls)
        if enabled is not _MISSING:
            profile.enabled = bool(enabled)
        self.session.flush()
        return self._profile_info(profile)

    def delete_profile(self, profile_id: str) -> None:
        normalized = self._normalized_profile_id(profile_id)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            raise HarborSettingsError(
                "harbor_profile_default_protected",
                "Default profile нельзя удалить",
            )
        self._assert_profile_mutable(normalized)
        profile = self._required_profile(normalized)
        self.profiles.delete(profile)
        root = self._profile_root(normalized)
        if root.exists():
            shutil.rmtree(root)

    def set_url(self, value: str | None) -> None:
        self._assert_profile_mutable(DEFAULT_HARBOR_PROFILE_ID)
        self.metadata.set_value(HARBOR_URL_KEY, self._validated_url(value) or "")

    def set_username(self, value: str | None) -> None:
        self._assert_profile_mutable(DEFAULT_HARBOR_PROFILE_ID)
        self.metadata.set_value(HARBOR_USERNAME_KEY, self._normalized_username(value) or "")

    def set_verify_tls(self, value: bool) -> None:
        self._assert_profile_mutable(DEFAULT_HARBOR_PROFILE_ID)
        self.metadata.set_value(HARBOR_VERIFY_TLS_KEY, "true" if value else "false")

    def credential_configured(self, profile_id: str | None = None) -> bool:
        normalized = self._normalized_profile_id(profile_id)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            return self._read_default_password() is not None
        self._required_profile(normalized)
        return self._profile_secret_path(normalized).is_file()

    def custom_ca_configured(self, profile_id: str | None = None) -> bool:
        normalized = self._normalized_profile_id(profile_id)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            return self.settings.harbor_managed_ca_file.is_file()
        self._required_profile(normalized)
        return self._profile_ca_path(normalized).is_file()

    def rotate_credential(self, secret: str, profile_id: str | None = None) -> None:
        if not secret:
            raise HarborSettingsError("harbor_secret_empty", "Credential не может быть пустым")
        normalized = self._normalized_profile_id(profile_id)
        self._assert_profile_mutable(normalized)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            target = self.settings.harbor_managed_secret_file
        else:
            self._required_profile(normalized)
            target = self._profile_secret_path(normalized)
        self._atomic_write(target, secret, 0o600)

    def install_ca(self, certificate_pem: str, profile_id: str | None = None) -> None:
        normalized = self._normalized_profile_id(profile_id)
        self._assert_profile_mutable(normalized)
        if normalized != DEFAULT_HARBOR_PROFILE_ID:
            self._required_profile(normalized)
        encoded = certificate_pem.encode("utf-8")
        if len(encoded) > self.settings.harbor_ca_max_bytes:
            raise HarborSettingsError(
                "harbor_ca_too_large",
                "CA bundle превышает допустимый размер",
            )
        begin_marker = "-----BEGIN CERTIFICATE-----"
        end_marker = "-----END CERTIFICATE-----"
        if begin_marker not in certificate_pem or end_marker not in certificate_pem:
            raise HarborSettingsError("harbor_ca_invalid", "Ожидается PEM-сертификат CA")

        target = (
            self.settings.harbor_managed_ca_file
            if normalized == DEFAULT_HARBOR_PROFILE_ID
            else self._profile_ca_path(normalized)
        )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".harbor-ca-", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            try:
                ssl.create_default_context(cafile=str(temp_path))
            except (OSError, ssl.SSLError) as exc:
                raise HarborSettingsError(
                    "harbor_ca_invalid",
                    "CA bundle не удалось загрузить как доверенный PEM",
                ) from exc
            os.replace(temp_path, target)
            os.chmod(target, 0o600)
        finally:
            temp_path.unlink(missing_ok=True)

    def remove_managed_ca(self, profile_id: str | None = None) -> None:
        normalized = self._normalized_profile_id(profile_id)
        self._assert_profile_mutable(normalized)
        if normalized == DEFAULT_HARBOR_PROFILE_ID:
            self.settings.harbor_managed_ca_file.unlink(missing_ok=True)
            return
        self._required_profile(normalized)
        self._profile_ca_path(normalized).unlink(missing_ok=True)

    def _resolve_default(self) -> EffectiveHarborSettings:
        url_override = self.metadata.get_value(HARBOR_URL_KEY)
        username_override = self.metadata.get_value(HARBOR_USERNAME_KEY)
        verify_override = self.metadata.get_value(HARBOR_VERIFY_TLS_KEY)

        url = (
            self._validated_url(url_override)
            if url_override is not None
            else (str(self.settings.harbor_url).rstrip("/") if self.settings.harbor_url else None)
        )
        username = (
            (username_override or None)
            if username_override is not None
            else self.settings.harbor_user
        )
        verify_tls = (
            verify_override == "true"
            if verify_override is not None
            else self.settings.harbor_verify_tls
        )
        password = self._read_default_password()
        ca_file = self._default_ca_file() if verify_tls else None
        return EffectiveHarborSettings(
            url=url,
            username=username,
            password=password,
            verify_tls=verify_tls,
            ca_file=ca_file,
        )

    def _profile_info(self, profile: HarborProfile) -> HarborProfileInfo:
        return HarborProfileInfo(
            id=profile.id,
            name=profile.name,
            url=profile.url,
            username=profile.username,
            verify_tls=profile.verify_tls,
            enabled=profile.enabled,
            credential_configured=self._profile_secret_path(profile.id).is_file(),
            custom_ca_configured=self._profile_ca_path(profile.id).is_file(),
        )

    def _required_profile(self, profile_id: str) -> HarborProfile:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise HarborSettingsError(
                "harbor_profile_not_found",
                "Harbor profile не найден",
            )
        return profile

    def _assert_unique_name(
        self,
        name: str,
        *,
        exclude_profile_id: str | None = None,
    ) -> None:
        if name.casefold() == "default":
            raise HarborSettingsError(
                "harbor_profile_name_conflict",
                "Имя Default зарезервировано для backward-compatible профиля",
            )
        for profile in self.profiles.list():
            if profile.id != exclude_profile_id and profile.name.casefold() == name.casefold():
                raise HarborSettingsError(
                    "harbor_profile_name_conflict",
                    "Harbor profile с таким именем уже существует",
                )

    def _assert_profile_mutable(self, profile_id: str) -> None:
        profile_filter = (
            or_(
                Operation.harbor_profile_id == DEFAULT_HARBOR_PROFILE_ID,
                Operation.harbor_profile_id.is_(None),
            )
            if profile_id == DEFAULT_HARBOR_PROFILE_ID
            else Operation.harbor_profile_id == profile_id
        )
        active = self.session.scalar(
            select(Operation.id)
            .where(
                profile_filter,
                Operation.status.not_in(tuple(TERMINAL_STATES)),
            )
            .limit(1)
        )
        if active is not None:
            raise HarborSettingsError(
                "harbor_profile_in_use",
                "Harbor profile используется незавершённой transfer operation",
            )

    def _read_default_password(self) -> str | None:
        for path in (
            self.settings.harbor_managed_secret_file,
            self.settings.harbor_password_file,
        ):
            if path is not None and path.is_file():
                value = path.read_text(encoding="utf-8").rstrip("\r\n")
                if value:
                    return value
        if self.settings.harbor_password is not None:
            value = self.settings.harbor_password.get_secret_value()
            return value if value else None
        return None

    def _read_profile_password(self, profile_id: str) -> str | None:
        path = self._profile_secret_path(profile_id)
        if not path.is_file():
            return None
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
        return value if value else None

    def _default_ca_file(self) -> Path | None:
        if self.settings.harbor_managed_ca_file.is_file():
            return self.settings.harbor_managed_ca_file
        return self.settings.harbor_ca_file

    def _profile_root(self, profile_id: str) -> Path:
        base = self.settings.harbor_managed_secret_file.parent.resolve() / "harbor-profiles"
        return (base / profile_id).resolve()

    def _profile_secret_path(self, profile_id: str) -> Path:
        return self._profile_root(profile_id) / "credential"

    def _profile_ca_path(self, profile_id: str) -> Path:
        return self._profile_root(profile_id) / "ca.pem"

    @staticmethod
    def _normalized_username(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _validated_profile_name(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise HarborSettingsError(
                "harbor_profile_name_invalid",
                "Имя Harbor profile должно содержать от 1 до 128 символов",
            )
        return normalized

    @staticmethod
    def _normalized_profile_id(value: str | None) -> str:
        if value is None or value == DEFAULT_HARBOR_PROFILE_ID:
            return DEFAULT_HARBOR_PROFILE_ID
        normalized = value.strip().lower()
        if not _PROFILE_ID_PATTERN.fullmatch(normalized):
            raise HarborSettingsError(
                "harbor_profile_id_invalid",
                "Harbor profile id имеет неверный формат",
            )
        return normalized

    @staticmethod
    def _validated_url(value: str | None) -> str | None:
        if value is None or not value:
            return None
        try:
            parsed = _HARBOR_URL_ADAPTER.validate_python(value)
            validated = validate_harbor_base_url(parsed)
        except ValueError as exc:
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "URL локального Harbor некорректен",
            ) from exc
        return str(validated).rstrip("/") if validated is not None else None

    @staticmethod
    def _atomic_write(path: Path, value: str, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, path)
            os.chmod(path, mode)
        finally:
            temp_path.unlink(missing_ok=True)
