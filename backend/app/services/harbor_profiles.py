from __future__ import annotations

import json
import os
import ssl
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Operation
from app.db.repositories import SettingMetadataRepository
from app.services.harbor_client import HarborClient
from app.services.harbor_settings import (
    EffectiveHarborSettings,
    HarborSettingsError,
    HarborSettingsService,
)

PROFILES_KEY = "harbor.profiles.v1"
DEFAULT_PROFILE_ID = "default"


@dataclass(frozen=True, slots=True)
class HarborProfile:
    id: str
    name: str
    url: str
    username: str | None
    verify_tls: bool
    enabled: bool
    is_default: bool = False


class HarborProfileService:
    """Persistent Harbor profiles layered over the legacy single-Harbor settings contract."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.metadata = SettingMetadataRepository(session)
        self.legacy = HarborSettingsService(session, settings)

    def list_profiles(self) -> list[HarborProfile]:
        profiles = [self._default_profile()]
        profiles.extend(self._load_additional())
        return profiles

    def get(self, profile_id: str) -> HarborProfile:
        normalized = profile_id.strip()
        if normalized == DEFAULT_PROFILE_ID:
            return self._default_profile()
        for profile in self._load_additional():
            if profile.id == normalized:
                return profile
        raise HarborSettingsError("harbor_profile_not_found", "Профиль Harbor не найден")

    def selectable_profiles(self) -> list[HarborProfile]:
        return [profile for profile in self.list_profiles() if profile.enabled and profile.url]

    def resolve(self, profile_id: str) -> EffectiveHarborSettings:
        if profile_id == DEFAULT_PROFILE_ID:
            resolved = self.legacy.resolve()
            if not resolved.url:
                raise HarborSettingsError("harbor_not_configured", "Локальный Harbor не настроен")
            return resolved

        profile = self.get(profile_id)
        if not profile.enabled:
            raise HarborSettingsError("harbor_profile_disabled", "Профиль Harbor отключён")
        if not profile.url:
            raise HarborSettingsError("harbor_not_configured", "URL профиля Harbor не настроен")
        password = self._read_secret(self._credential_path(profile))
        ca_file = self._ca_path(profile) if profile.verify_tls and self._ca_path(profile).is_file() else None
        return EffectiveHarborSettings(
            url=profile.url,
            username=profile.username,
            password=password,
            verify_tls=profile.verify_tls,
            ca_file=ca_file,
        )

    def operation_snapshot(self, profile_id: str) -> HarborProfile:
        profile = self.get(profile_id)
        if not profile.enabled or not profile.url:
            raise HarborSettingsError(
                "harbor_profile_disabled",
                "Профиль Harbor недоступен для новой операции",
            )
        return profile

    def assert_operation_binding(self, operation: Operation) -> HarborProfile:
        profile_id = operation.harbor_profile_id or DEFAULT_PROFILE_ID
        profile = self.operation_snapshot(profile_id)
        if operation.harbor_profile_id is None:
            return profile
        if (
            operation.harbor_profile_name != profile.name
            or operation.harbor_profile_url != profile.url
        ):
            raise HarborSettingsError(
                "harbor_profile_changed",
                "Harbor profile изменился после создания операции",
            )
        return profile

    def create(
        self,
        *,
        name: str,
        url: str,
        username: str | None,
        verify_tls: bool,
        enabled: bool,
    ) -> HarborProfile:
        normalized_name = self._normalize_name(name)
        validated_url = HarborSettingsService._validated_url(url)
        if validated_url is None:
            raise HarborSettingsError("harbor_configuration_invalid", "URL Harbor обязателен")
        profiles = self._load_additional()
        self._assert_unique_name(normalized_name, profiles)

        profile = HarborProfile(
            id=uuid4().hex,
            name=normalized_name,
            url=validated_url,
            username=self._normalize_username(username),
            verify_tls=verify_tls,
            enabled=enabled,
        )
        profiles.append(profile)
        self._save_additional(profiles)
        return profile

    def update(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        username: str | None = None,
        username_set: bool = False,
        verify_tls: bool | None = None,
        enabled: bool | None = None,
    ) -> HarborProfile:
        if profile_id == DEFAULT_PROFILE_ID:
            raise HarborSettingsError(
                "harbor_profile_default_managed_elsewhere",
                "Default Harbor profile изменяется через существующие настройки Harbor",
            )

        profiles = self._load_additional()
        current = self.get(profile_id)
        next_name = self._normalize_name(name) if name is not None else current.name
        self._assert_unique_name(next_name, profiles, exclude_id=current.id)

        next_url = current.url
        if url is not None:
            validated_url = HarborSettingsService._validated_url(url)
            if validated_url is None:
                raise HarborSettingsError("harbor_configuration_invalid", "URL Harbor обязателен")
            next_url = validated_url

        updated = replace(
            current,
            name=next_name,
            url=next_url,
            username=self._normalize_username(username) if username_set else current.username,
            verify_tls=verify_tls if verify_tls is not None else current.verify_tls,
            enabled=enabled if enabled is not None else current.enabled,
        )
        self._save_additional([updated if item.id == current.id else item for item in profiles])
        return updated

    def delete(self, profile_id: str) -> None:
        if profile_id == DEFAULT_PROFILE_ID:
            raise HarborSettingsError(
                "harbor_profile_default_protected",
                "Default Harbor profile нельзя удалить",
            )
        profiles = self._load_additional()
        current = self.get(profile_id)
        referenced = self.session.scalar(
            select(Operation.id).where(Operation.harbor_profile_id == current.id).limit(1)
        )
        if referenced is not None:
            raise HarborSettingsError(
                "harbor_profile_in_use",
                "Профиль Harbor используется сохранёнными операциями и не может быть удалён",
            )
        self._save_additional([item for item in profiles if item.id != current.id])
        self._credential_path(current).unlink(missing_ok=True)
        self._ca_path(current).unlink(missing_ok=True)
        profile_dir = self._profile_dir(current)
        try:
            profile_dir.rmdir()
        except OSError:
            pass

    def build_client(self, profile_id: str) -> HarborClient:
        resolved = self.resolve(profile_id)
        verify: bool | str = resolved.verify_tls
        if resolved.verify_tls and resolved.ca_file is not None:
            verify = str(resolved.ca_file)
        return HarborClient(
            base_url=resolved.url or "",
            username=resolved.username,
            password=resolved.password,
            verify=verify,
            connect_timeout=self.settings.harbor_connect_timeout_seconds,
            read_timeout=self.settings.harbor_read_timeout_seconds,
        )

    def credential_configured(self, profile: HarborProfile) -> bool:
        if profile.is_default:
            return self.legacy.credential_configured()
        return self._read_secret(self._credential_path(profile)) is not None

    def custom_ca_configured(self, profile: HarborProfile) -> bool:
        if profile.is_default:
            return self.legacy.custom_ca_configured()
        return self._ca_path(profile).is_file()

    def rotate_credential(self, profile_id: str, secret: str) -> None:
        profile = self.get(profile_id)
        if profile.is_default:
            self.legacy.rotate_credential(secret)
            return
        if not secret:
            raise HarborSettingsError("harbor_secret_empty", "Credential не может быть пустым")
        self._atomic_write(self._credential_path(profile), secret.encode("utf-8"), 0o600)

    def install_ca(self, profile_id: str, certificate_pem: str) -> None:
        profile = self.get(profile_id)
        if profile.is_default:
            self.legacy.install_ca(certificate_pem)
            return

        encoded = certificate_pem.encode("utf-8")
        if len(encoded) > self.settings.harbor_ca_max_bytes:
            raise HarborSettingsError(
                "harbor_ca_too_large",
                "CA bundle превышает допустимый размер",
            )
        if (
            "-----BEGIN CERTIFICATE-----" not in certificate_pem
            or "-----END CERTIFICATE-----" not in certificate_pem
        ):
            raise HarborSettingsError("harbor_ca_invalid", "Ожидается PEM-сертификат CA")

        target = self._ca_path(profile)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".ca-", dir=target.parent)
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

    def remove_ca(self, profile_id: str) -> None:
        profile = self.get(profile_id)
        if profile.is_default:
            self.legacy.remove_managed_ca()
            return
        self._ca_path(profile).unlink(missing_ok=True)

    def _default_profile(self) -> HarborProfile:
        resolved = self.legacy.resolve()
        return HarborProfile(
            id=DEFAULT_PROFILE_ID,
            name="Default Harbor",
            url=resolved.url or "",
            username=resolved.username,
            verify_tls=resolved.verify_tls,
            enabled=resolved.url is not None,
            is_default=True,
        )

    def _load_additional(self) -> list[HarborProfile]:
        raw = self.metadata.get_value(PROFILES_KEY)
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HarborSettingsError(
                "harbor_profiles_invalid",
                "Сохранённая конфигурация Harbor profiles повреждена",
            ) from exc
        if not isinstance(payload, list):
            raise HarborSettingsError(
                "harbor_profiles_invalid",
                "Сохранённая конфигурация Harbor profiles повреждена",
            )

        profiles: list[HarborProfile] = []
        try:
            for item in payload:
                if not isinstance(item, dict):
                    raise TypeError
                profile = HarborProfile(
                    id=str(item["id"]),
                    name=self._normalize_name(str(item["name"])),
                    url=HarborSettingsService._validated_url(str(item["url"])) or "",
                    username=self._normalize_username(item.get("username")),
                    verify_tls=bool(item["verify_tls"]),
                    enabled=bool(item["enabled"]),
                )
                if not profile.id or profile.id == DEFAULT_PROFILE_ID or not profile.url:
                    raise ValueError
                profiles.append(profile)
        except (KeyError, TypeError, ValueError, HarborSettingsError) as exc:
            raise HarborSettingsError(
                "harbor_profiles_invalid",
                "Сохранённая конфигурация Harbor profiles повреждена",
            ) from exc
        if len({item.id for item in profiles}) != len(profiles):
            raise HarborSettingsError("harbor_profiles_invalid", "Harbor profile id продублирован")
        return profiles

    def _save_additional(self, profiles: list[HarborProfile]) -> None:
        payload = [
            {
                "id": item.id,
                "name": item.name,
                "url": item.url,
                "username": item.username,
                "verify_tls": item.verify_tls,
                "enabled": item.enabled,
            }
            for item in sorted(profiles, key=lambda value: value.name.casefold())
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.metadata.set_value(PROFILES_KEY, encoded)

    def _profile_dir(self, profile: HarborProfile) -> Path:
        return self.settings.harbor_managed_secret_file.parent / "harbor-profiles" / profile.id

    def _credential_path(self, profile: HarborProfile) -> Path:
        return self._profile_dir(profile) / "credential"

    def _ca_path(self, profile: HarborProfile) -> Path:
        return self._profile_dir(profile) / "ca.pem"

    @staticmethod
    def _read_secret(path: Path) -> str | None:
        if not path.is_file():
            return None
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
        return value or None

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise HarborSettingsError("harbor_profile_name_invalid", "Имя профиля обязательно")
        if len(normalized) > 128:
            raise HarborSettingsError(
                "harbor_profile_name_invalid",
                "Имя профиля слишком длинное",
            )
        return normalized

    @staticmethod
    def _normalize_username(value: object | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if len(normalized) > 256:
            raise HarborSettingsError(
                "harbor_profile_username_invalid",
                "Username профиля слишком длинный",
            )
        return normalized or None

    @staticmethod
    def _assert_unique_name(
        name: str,
        profiles: list[HarborProfile],
        *,
        exclude_id: str | None = None,
    ) -> None:
        folded = name.casefold()
        if folded == "default harbor":
            raise HarborSettingsError(
                "harbor_profile_name_conflict",
                "Имя профиля конфликтует с default profile",
            )
        if any(
            item.id != exclude_id and item.name.casefold() == folded
            for item in profiles
        ):
            raise HarborSettingsError(
                "harbor_profile_name_conflict",
                "Профиль Harbor с таким именем уже существует",
            )

    @staticmethod
    def _atomic_write(path: Path, data: bytes, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, path)
            os.chmod(path, mode)
        finally:
            temp_path.unlink(missing_ok=True)
