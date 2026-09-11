from __future__ import annotations

import json
import os
import ssl
import tempfile
from pathlib import Path
from typing import Any, Final

from pydantic import AnyHttpUrl, SecretStr, TypeAdapter
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import AuditEvent, SettingMetadata, User

HARBOR_URL_KEY: Final = "harbor.url"
HARBOR_USER_KEY: Final = "harbor.user"
HARBOR_VERIFY_TLS_KEY: Final = "harbor.verify_tls"
HARBOR_CA_MODE_KEY: Final = "harbor.ca_mode"
CA_MODE_RUNTIME: Final = "runtime"
CA_MODE_NONE: Final = "none"

_MISSING = object()
_HTTP_URL = TypeAdapter(AnyHttpUrl)


class HarborSettingsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _read_metadata(session: Session, key: str) -> Any:
    row = session.get(SettingMetadata, key)
    if row is None:
        return _MISSING
    try:
        return json.loads(row.value)
    except json.JSONDecodeError as exc:
        raise HarborSettingsError(
            "harbor_configuration_invalid",
            "Сохранённая конфигурация локального Harbor повреждена",
        ) from exc


def set_metadata(session: Session, key: str, value: Any, *, description: str) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    row = session.get(SettingMetadata, key)
    if row is None:
        row = SettingMetadata(key=key, value=encoded, description=description)
        session.add(row)
    else:
        row.value = encoded
        row.description = description
    session.flush()


def _read_secret_file(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarborSettingsError(
            "harbor_secret_unreadable",
            "Не удалось прочитать файл учётных данных локального Harbor",
        ) from exc
    if not value:
        raise HarborSettingsError(
            "harbor_secret_invalid",
            "Файл учётных данных локального Harbor пуст",
        )
    return value.rstrip("\r\n")


def _atomic_write_private(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            os.chmod(temp_path, 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            if "temp_path" in locals() and temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        raise HarborSettingsError(
            "harbor_secret_write_failed",
            "Не удалось безопасно сохранить конфигурационный файл Harbor",
        ) from exc


def validate_ca_pem(certificate_pem: str) -> None:
    try:
        ssl.create_default_context(cadata=certificate_pem)
    except (ssl.SSLError, ValueError) as exc:
        raise HarborSettingsError(
            "harbor_ca_invalid",
            "Загруженный CA не является корректным PEM-сертификатом",
        ) from exc


def rotate_harbor_credential(settings: Settings, secret: str) -> None:
    if not secret:
        raise HarborSettingsError(
            "harbor_secret_invalid",
            "Учётные данные Harbor не могут быть пустыми",
        )
    _atomic_write_private(settings.harbor_runtime_password_file, secret)


def install_runtime_ca(settings: Settings, certificate_pem: str) -> None:
    validate_ca_pem(certificate_pem)
    _atomic_write_private(settings.harbor_runtime_ca_file, certificate_pem)


def remove_runtime_ca(settings: Settings) -> None:
    try:
        settings.harbor_runtime_ca_file.unlink(missing_ok=True)
    except OSError as exc:
        raise HarborSettingsError(
            "harbor_ca_remove_failed",
            "Не удалось удалить runtime CA локального Harbor",
        ) from exc


def resolve_harbor_settings(session: Session, bootstrap: Settings) -> Settings:
    updates: dict[str, Any] = {}

    stored_url = _read_metadata(session, HARBOR_URL_KEY)
    if stored_url is not _MISSING:
        if stored_url is None:
            updates["harbor_url"] = None
        elif isinstance(stored_url, str):
            try:
                updates["harbor_url"] = _HTTP_URL.validate_python(stored_url)
            except ValueError as exc:
                raise HarborSettingsError(
                    "harbor_configuration_invalid",
                    "Сохранённый URL локального Harbor некорректен",
                ) from exc
        else:
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "Сохранённый URL локального Harbor некорректен",
            )

    stored_user = _read_metadata(session, HARBOR_USER_KEY)
    if stored_user is not _MISSING:
        if stored_user is not None and not isinstance(stored_user, str):
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "Сохранённое имя пользователя Harbor некорректно",
            )
        updates["harbor_user"] = stored_user

    stored_verify_tls = _read_metadata(session, HARBOR_VERIFY_TLS_KEY)
    if stored_verify_tls is not _MISSING:
        if not isinstance(stored_verify_tls, bool):
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "Сохранённая TLS-настройка Harbor некорректна",
            )
        updates["harbor_verify_tls"] = stored_verify_tls

    runtime_password = bootstrap.harbor_runtime_password_file
    if runtime_password.is_file():
        updates["harbor_password"] = SecretStr(_read_secret_file(runtime_password))
    elif bootstrap.harbor_password_file is not None and bootstrap.harbor_password_file.is_file():
        updates["harbor_password"] = SecretStr(_read_secret_file(bootstrap.harbor_password_file))

    ca_mode = _read_metadata(session, HARBOR_CA_MODE_KEY)
    if ca_mode == CA_MODE_RUNTIME:
        if not bootstrap.harbor_runtime_ca_file.is_file():
            raise HarborSettingsError(
                "harbor_ca_unreadable",
                "Сохранённый runtime CA локального Harbor отсутствует",
            )
        updates["harbor_ca_file"] = bootstrap.harbor_runtime_ca_file
    elif ca_mode == CA_MODE_NONE:
        updates["harbor_ca_file"] = None
    elif ca_mode is not _MISSING:
        raise HarborSettingsError(
            "harbor_configuration_invalid",
            "Сохранённый режим CA локального Harbor некорректен",
        )

    return bootstrap.model_copy(update=updates)


def credential_configured(settings: Settings) -> bool:
    return settings.harbor_password is not None and bool(settings.harbor_password.get_secret_value())


def ca_source(settings: Settings, bootstrap: Settings) -> str | None:
    if settings.harbor_ca_file is None:
        return None
    if settings.harbor_ca_file == bootstrap.harbor_runtime_ca_file:
        return "runtime"
    return "bootstrap"


def record_harbor_audit(
    session: Session,
    *,
    actor: User,
    event_type: str,
    changed_fields: list[str],
    result: str = "success",
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor.id,
        actor_username=actor.username,
        event_type=event_type,
        result=result,
        metadata_json=json.dumps(
            {"changed_fields": sorted(set(changed_fields))},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    session.add(event)
    session.flush()
    return event
