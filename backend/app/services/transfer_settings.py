from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.repositories import SettingMetadataRepository

logger = logging.getLogger(__name__)

IMPORT_ALLOW_OVERWRITE_KEY = "transfer.import_allow_overwrite"
IMPORT_MAX_UPLOAD_BYTES_KEY = "transfer.import_max_upload_bytes"
BUNDLE_MAX_ARCHIVE_BYTES_KEY = "transfer.bundle_max_archive_bytes"
BUNDLE_MAX_EXTRACTED_BYTES_KEY = "transfer.bundle_max_extracted_bytes"
BUNDLE_MAX_MEMBER_COUNT_KEY = "transfer.bundle_max_member_count"
OPERATION_MAX_CONCURRENT_KEY = "transfer.operation_max_concurrent"

HOT_FIELDS = (
    "import_allow_overwrite",
    "import_max_upload_bytes",
    "bundle_max_archive_bytes",
    "bundle_max_extracted_bytes",
    "bundle_max_member_count",
)
RESTART_REQUIRED_FIELDS = ("operation_max_concurrent",)

MIN_BUNDLE_BYTES = 1024**2
MAX_ARCHIVE_BYTES = 1024**4
MAX_EXTRACTED_BYTES = 2 * 1024**4
MIN_MEMBER_COUNT = 4
MAX_MEMBER_COUNT = 1_000_000
MIN_OPERATION_CONCURRENCY = 1
MAX_OPERATION_CONCURRENCY = 32


@dataclass(slots=True)
class TransferSettingsError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class TransferSettingsSnapshot:
    import_allow_overwrite: bool
    import_max_upload_bytes: int
    bundle_max_archive_bytes: int
    bundle_max_extracted_bytes: int
    bundle_max_member_count: int
    operation_max_concurrent: int
    operation_max_concurrent_active: int
    restart_required_fields: tuple[str, ...]


class TransferSettingsService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.metadata = SettingMetadataRepository(session)

    def resolve(self) -> TransferSettingsSnapshot:
        values = {
            "import_allow_overwrite": self._read_bool(
                IMPORT_ALLOW_OVERWRITE_KEY,
                self.settings.import_allow_overwrite,
            ),
            "import_max_upload_bytes": self._read_int(
                IMPORT_MAX_UPLOAD_BYTES_KEY,
                self.settings.import_max_upload_bytes,
            ),
            "bundle_max_archive_bytes": self._read_int(
                BUNDLE_MAX_ARCHIVE_BYTES_KEY,
                self.settings.bundle_max_archive_bytes,
            ),
            "bundle_max_extracted_bytes": self._read_int(
                BUNDLE_MAX_EXTRACTED_BYTES_KEY,
                self.settings.bundle_max_extracted_bytes,
            ),
            "bundle_max_member_count": self._read_int(
                BUNDLE_MAX_MEMBER_COUNT_KEY,
                self.settings.bundle_max_member_count,
            ),
            "operation_max_concurrent": self._read_int(
                OPERATION_MAX_CONCURRENT_KEY,
                self.settings.operation_max_concurrent,
            ),
        }
        self.validate(values)
        restart_required = (
            ("operation_max_concurrent",)
            if values["operation_max_concurrent"] != self.settings.operation_max_concurrent
            else ()
        )
        return TransferSettingsSnapshot(
            **values,
            operation_max_concurrent_active=self.settings.operation_max_concurrent,
            restart_required_fields=restart_required,
        )

    def effective_settings(self, *, include_restart_required: bool = False) -> Settings:
        snapshot = self.resolve()
        updates: dict[str, object] = {
            field: getattr(snapshot, field)
            for field in HOT_FIELDS
        }
        if include_restart_required:
            updates["operation_max_concurrent"] = snapshot.operation_max_concurrent
        return self.settings.model_copy(update=updates)

    def set_values(self, values: dict[str, bool | int]) -> None:
        unknown = set(values) - set(HOT_FIELDS) - set(RESTART_REQUIRED_FIELDS)
        if unknown:
            raise TransferSettingsError(
                "transfer_setting_unknown",
                "Обнаружен неподдерживаемый transfer setting",
            )

        current = self.resolve()
        merged: dict[str, bool | int] = {
            field: getattr(current, field)
            for field in (*HOT_FIELDS, *RESTART_REQUIRED_FIELDS)
        }
        merged.update(values)
        self.validate(merged)

        keys = {
            "import_allow_overwrite": IMPORT_ALLOW_OVERWRITE_KEY,
            "import_max_upload_bytes": IMPORT_MAX_UPLOAD_BYTES_KEY,
            "bundle_max_archive_bytes": BUNDLE_MAX_ARCHIVE_BYTES_KEY,
            "bundle_max_extracted_bytes": BUNDLE_MAX_EXTRACTED_BYTES_KEY,
            "bundle_max_member_count": BUNDLE_MAX_MEMBER_COUNT_KEY,
            "operation_max_concurrent": OPERATION_MAX_CONCURRENT_KEY,
        }
        for field, value in values.items():
            serialized = (
                "true" if value is True else "false" if value is False else str(value)
            )
            self.metadata.set_value(keys[field], serialized)

    @staticmethod
    def validate(values: dict[str, bool | int]) -> None:
        upload = int(values["import_max_upload_bytes"])
        archive = int(values["bundle_max_archive_bytes"])
        extracted = int(values["bundle_max_extracted_bytes"])
        members = int(values["bundle_max_member_count"])
        concurrency = int(values["operation_max_concurrent"])

        if not MIN_BUNDLE_BYTES <= upload <= MAX_ARCHIVE_BYTES:
            raise TransferSettingsError(
                "transfer_upload_limit_invalid",
                "Browser upload limit должен быть от 1 MiB до 1 TiB",
            )
        if not MIN_BUNDLE_BYTES <= archive <= MAX_ARCHIVE_BYTES:
            raise TransferSettingsError(
                "transfer_archive_limit_invalid",
                "Archive limit должен быть от 1 MiB до 1 TiB",
            )
        if not MIN_BUNDLE_BYTES <= extracted <= MAX_EXTRACTED_BYTES:
            raise TransferSettingsError(
                "transfer_extracted_limit_invalid",
                "Extracted limit должен быть от 1 MiB до 2 TiB",
            )
        if not MIN_MEMBER_COUNT <= members <= MAX_MEMBER_COUNT:
            raise TransferSettingsError(
                "transfer_member_limit_invalid",
                "Archive member limit должен быть от 4 до 1000000",
            )
        if not MIN_OPERATION_CONCURRENCY <= concurrency <= MAX_OPERATION_CONCURRENCY:
            raise TransferSettingsError(
                "transfer_concurrency_invalid",
                "Operation concurrency должен быть от 1 до 32",
            )
        if upload > archive:
            raise TransferSettingsError(
                "transfer_limits_inconsistent",
                "Browser upload limit не может превышать archive limit",
            )
        if extracted < archive:
            raise TransferSettingsError(
                "transfer_limits_inconsistent",
                "Extracted limit не может быть меньше archive limit",
            )

    def _read_bool(self, key: str, fallback: bool) -> bool:
        raw = self.metadata.get_value(key)
        if raw is None:
            return fallback
        if raw == "true":
            return True
        if raw == "false":
            return False
        raise TransferSettingsError(
            "transfer_settings_persisted_invalid",
            "Сохранённое значение transfer settings некорректно",
        )

    def _read_int(self, key: str, fallback: int) -> int:
        raw = self.metadata.get_value(key)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except ValueError as exc:
            raise TransferSettingsError(
                "transfer_settings_persisted_invalid",
                "Сохранённое значение transfer settings некорректно",
            ) from exc


def load_runtime_transfer_settings(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> Settings:
    with session_factory() as session:
        return TransferSettingsService(session, settings).effective_settings()


def load_startup_transfer_settings(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> Settings:
    """Apply persisted transfer settings when the process starts.

    The settings table may not exist yet during installation/migration tooling, so startup
    falls back to deployment values instead of making the API process unrecoverable.
    """

    try:
        with session_factory() as session:
            return TransferSettingsService(session, settings).effective_settings(
                include_restart_required=True
            )
    except (SQLAlchemyError, TransferSettingsError):
        logger.warning(
            "persisted transfer settings unavailable during startup; using deployment defaults",
            exc_info=True,
        )
        return settings
