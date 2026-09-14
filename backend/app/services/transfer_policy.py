from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.repositories import SettingMetadataRepository

_MIB: Final = 1024**2
_TIB: Final = 1024**4

POLICY_FIELDS: Final[tuple[str, ...]] = (
    "import_allow_overwrite",
    "import_max_upload_bytes",
    "bundle_max_archive_bytes",
    "bundle_max_extracted_bytes",
    "bundle_max_member_count",
    "operation_disk_reserve_bytes",
    "operation_max_concurrent",
)

RESTART_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset({"operation_max_concurrent"})

_KEYS: Final[dict[str, str]] = {
    field: f"transfer.{field}" for field in POLICY_FIELDS
}

_BOUNDS: Final[dict[str, tuple[int, int]]] = {
    "import_max_upload_bytes": (_MIB, _TIB),
    "bundle_max_archive_bytes": (_MIB, _TIB),
    "bundle_max_extracted_bytes": (_MIB, 2 * _TIB),
    "bundle_max_member_count": (4, 1_000_000),
    "operation_disk_reserve_bytes": (0, _TIB),
    "operation_max_concurrent": (1, 32),
}


@dataclass(frozen=True, slots=True)
class TransferPolicySnapshot:
    import_allow_overwrite: bool
    import_max_upload_bytes: int
    bundle_max_archive_bytes: int
    bundle_max_extracted_bytes: int
    bundle_max_member_count: int
    operation_disk_reserve_bytes: int
    operation_max_concurrent: int
    effective_operation_max_concurrent: int
    restart_required_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransferPolicyChange:
    changed_fields: tuple[str, ...]
    before: dict[str, int | bool]
    after: dict[str, int | bool]
    snapshot: TransferPolicySnapshot


@dataclass(slots=True)
class TransferPolicyError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class TransferPolicyService:
    """Resolve and persist the deliberately small set of admin-managed transfer policies."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.metadata = SettingMetadataRepository(session)

    def resolve(self) -> TransferPolicySnapshot:
        configured = self._configured_values()
        restart_required: list[str] = []
        if configured["operation_max_concurrent"] != self.settings.operation_max_concurrent:
            restart_required.append("operation_max_concurrent")
        return TransferPolicySnapshot(
            import_allow_overwrite=bool(configured["import_allow_overwrite"]),
            import_max_upload_bytes=int(configured["import_max_upload_bytes"]),
            bundle_max_archive_bytes=int(configured["bundle_max_archive_bytes"]),
            bundle_max_extracted_bytes=int(configured["bundle_max_extracted_bytes"]),
            bundle_max_member_count=int(configured["bundle_max_member_count"]),
            operation_disk_reserve_bytes=int(configured["operation_disk_reserve_bytes"]),
            operation_max_concurrent=int(configured["operation_max_concurrent"]),
            effective_operation_max_concurrent=self.settings.operation_max_concurrent,
            restart_required_fields=tuple(restart_required),
        )

    def update(self, values: dict[str, int | bool]) -> TransferPolicyChange:
        unknown = sorted(set(values) - set(POLICY_FIELDS))
        if unknown:
            raise TransferPolicyError(
                "transfer_policy_unknown_field",
                f"Неподдерживаемые поля transfer policy: {', '.join(unknown)}",
            )

        current = self._configured_values()
        candidate = {**current, **values}
        self._validate(candidate)

        changed = tuple(sorted(field for field in values if candidate[field] != current[field]))
        before = {field: current[field] for field in changed}
        after = {field: candidate[field] for field in changed}

        for field in changed:
            self.metadata.set_value(_KEYS[field], self._serialize(candidate[field]))

        return TransferPolicyChange(
            changed_fields=changed,
            before=before,
            after=after,
            snapshot=self.resolve(),
        )

    def apply_runtime(self, values: dict[str, int | bool]) -> None:
        for field, value in values.items():
            if field not in RESTART_REQUIRED_FIELDS:
                setattr(self.settings, field, value)

    def apply_persisted_at_startup(self) -> None:
        configured = self._configured_values()
        self._validate(configured)
        for field, value in configured.items():
            setattr(self.settings, field, value)

    def _configured_values(self) -> dict[str, int | bool]:
        values: dict[str, int | bool] = {}
        for field in POLICY_FIELDS:
            raw = self.metadata.get_value(_KEYS[field])
            fallback = getattr(self.settings, field)
            if raw is None:
                values[field] = fallback
            elif isinstance(fallback, bool):
                if raw not in {"true", "false"}:
                    raise TransferPolicyError(
                        "transfer_policy_persisted_invalid",
                        f"Сохранённое значение {field} некорректно",
                    )
                values[field] = raw == "true"
            else:
                try:
                    values[field] = int(raw)
                except ValueError as exc:
                    raise TransferPolicyError(
                        "transfer_policy_persisted_invalid",
                        f"Сохранённое значение {field} некорректно",
                    ) from exc
        self._validate(values)
        return values

    @staticmethod
    def _serialize(value: int | bool) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _validate(values: dict[str, int | bool]) -> None:
        for field, (minimum, maximum) in _BOUNDS.items():
            value = values[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise TransferPolicyError(
                    "transfer_policy_out_of_range",
                    f"{field} должен быть в диапазоне {minimum}…{maximum}",
                )

        upload = int(values["import_max_upload_bytes"])
        archive = int(values["bundle_max_archive_bytes"])
        extracted = int(values["bundle_max_extracted_bytes"])
        if upload > archive:
            raise TransferPolicyError(
                "transfer_policy_inconsistent",
                "import_max_upload_bytes не должен превышать bundle_max_archive_bytes",
            )
        if archive > extracted:
            raise TransferPolicyError(
                "transfer_policy_inconsistent",
                "bundle_max_archive_bytes не должен превышать bundle_max_extracted_bytes",
            )
