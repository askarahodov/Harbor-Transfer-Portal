from __future__ import annotations

import os
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import AnyHttpUrl, TypeAdapter
from sqlalchemy.orm import Session

from app.config import Settings, validate_harbor_base_url
from app.db.repositories import SettingMetadataRepository
from app.services.harbor_client import HarborClient

HARBOR_URL_KEY = "harbor.url"
HARBOR_USERNAME_KEY = "harbor.username"
HARBOR_VERIFY_TLS_KEY = "harbor.verify_tls"
_HARBOR_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


@dataclass(frozen=True, slots=True)
class EffectiveHarborSettings:
    url: str | None
    username: str | None
    password: str | None
    verify_tls: bool
    ca_file: Path | None


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

    def resolve(self) -> EffectiveHarborSettings:
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
        password = self._read_password()
        ca_file = self._effective_ca_file() if verify_tls else None

        return EffectiveHarborSettings(
            url=url,
            username=username,
            password=password,
            verify_tls=verify_tls,
            ca_file=ca_file,
        )

    def build_client(self) -> HarborClient:
        resolved = self.resolve()
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

    def set_url(self, value: str | None) -> None:
        self.metadata.set_value(HARBOR_URL_KEY, self._validated_url(value) or "")

    def set_username(self, value: str | None) -> None:
        self.metadata.set_value(HARBOR_USERNAME_KEY, value or "")

    def set_verify_tls(self, value: bool) -> None:
        self.metadata.set_value(HARBOR_VERIFY_TLS_KEY, "true" if value else "false")

    def credential_configured(self) -> bool:
        return self._read_password() is not None

    def custom_ca_configured(self) -> bool:
        return self._effective_ca_file() is not None

    def rotate_credential(self, secret: str) -> None:
        if not secret:
            raise HarborSettingsError("harbor_secret_empty", "Credential не может быть пустым")
        self._atomic_write(self.settings.harbor_managed_secret_file, secret, 0o600)

    def install_ca(self, certificate_pem: str) -> None:
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

        target = self.settings.harbor_managed_ca_file
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

    def remove_managed_ca(self) -> None:
        self.settings.harbor_managed_ca_file.unlink(missing_ok=True)

    def _read_password(self) -> str | None:
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

    def _effective_ca_file(self) -> Path | None:
        if self.settings.harbor_managed_ca_file.is_file():
            return self.settings.harbor_managed_ca_file
        return self.settings.harbor_ca_file

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
