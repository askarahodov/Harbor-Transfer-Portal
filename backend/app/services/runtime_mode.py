from __future__ import annotations

import secrets
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import PortalContour, Settings
from app.db.models import Operation, User
from app.db.repositories import AuditEventRepository, SettingMetadataRepository
from app.domain.bundle import OperationStatus

_RUNTIME_MODE_KEY = "runtime.portal_mode"
_RUNTIME_MODE_DESCRIPTION = "Authoritative runtime SOURCE/TARGET mode"
_RUNTIME_MODE_VERSION_KEY = "runtime.portal_mode_version"
_RUNTIME_MODE_VERSION_DESCRIPTION = "Monotonic runtime mode revision"
_MODE_LOCK = RLock()
_PENDING_SWITCH_TOKEN: str | None = None
_PENDING_SWITCH_TARGET: PortalContour | None = None

BLOCKING_OPERATION_STATUSES = frozenset(
    {
        OperationStatus.CREATED,
        OperationStatus.VALIDATING,
        OperationStatus.RUNNING,
        OperationStatus.PACKAGING,
        OperationStatus.VERIFYING,
        OperationStatus.UPLOADED,
        OperationStatus.DISCOVERED,
        OperationStatus.READY,
        OperationStatus.IMPORTING,
        OperationStatus.VERIFYING_TARGET,
    }
)


@dataclass(slots=True)
class RuntimeModeError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class RuntimeModeSnapshot:
    mode: PortalContour
    version: int


@dataclass(frozen=True, slots=True)
class RuntimeModeSwitchResult:
    previous: PortalContour
    current: PortalContour
    changed: bool
    mode_version: int
    cancelled_operation_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeModeSwitchPreparation:
    token: str | None
    previous: RuntimeModeSnapshot
    target: PortalContour
    blocking_operation_ids: tuple[int, ...]


class RuntimeModeService:
    """Persistent authoritative runtime mode and its process-local serialization barrier.

    PORTAL_CONTOUR remains the bootstrap default only. Once the persistent setting exists,
    it wins on every application startup. The same process-wide lock serializes mode
    switches with mode-bound operation starts and short security-sensitive mutations for
    the v1 single-backend-instance model.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.metadata = SettingMetadataRepository(session)

    def initialize(self) -> PortalContour:
        with _MODE_LOCK:
            stored = self.metadata.get_value(_RUNTIME_MODE_KEY)
            changed = False
            if stored is None:
                mode = self.settings.portal_contour
                self.metadata.set_value(
                    _RUNTIME_MODE_KEY,
                    mode.value,
                    description=_RUNTIME_MODE_DESCRIPTION,
                )
                changed = True
            else:
                mode = self._parse_mode(stored)

            if self.metadata.get_value(_RUNTIME_MODE_VERSION_KEY) is None:
                self.metadata.set_value(
                    _RUNTIME_MODE_VERSION_KEY,
                    "1",
                    description=_RUNTIME_MODE_VERSION_DESCRIPTION,
                )
                changed = True

            if changed:
                self.session.commit()
            self.settings.portal_contour = mode
            return mode

    def current(self) -> PortalContour:
        return self.current_snapshot().mode

    def current_snapshot(self) -> RuntimeModeSnapshot:
        stored = self.metadata.get_value(_RUNTIME_MODE_KEY)
        version = self.metadata.get_value(_RUNTIME_MODE_VERSION_KEY)
        if stored is None or version is None:
            self.initialize()
            stored = self.metadata.get_value(_RUNTIME_MODE_KEY)
            version = self.metadata.get_value(_RUNTIME_MODE_VERSION_KEY)
        if stored is None or version is None:
            raise RuntimeModeError(
                "runtime_mode_invalid",
                "Persisted runtime mode state отсутствует после инициализации",
            )
        mode = self._parse_mode(stored)
        mode_version = self._parse_version(version)
        self.settings.portal_contour = mode
        return RuntimeModeSnapshot(mode=mode, version=mode_version)

    @contextmanager
    def mode_guard(
        self,
        required_mode: PortalContour | None = None,
    ) -> Iterator[RuntimeModeSnapshot]:
        """Hold the runtime-mode lock across a short mode-sensitive critical section.

        The guard is intentionally process-local because the v1 deployment runs one
        backend instance against SQLite. A concurrent switch either completes first and
        the guarded action sees the new mode, or waits until the guarded action exits.
        """

        with _MODE_LOCK:
            self._require_no_pending_switch()
            snapshot = self.current_snapshot()
            if required_mode is not None and snapshot.mode is not required_mode:
                message = (
                    f"Операция требует режим {required_mode.value}, "
                    f"текущий режим {snapshot.mode.value}"
                )
                raise RuntimeModeError("runtime_mode_mismatch", message)
            # End the read transaction while keeping the process-wide barrier. This lets
            # the guarded caller safely commit through the same or another SQLAlchemy
            # Session without retaining a SQLite shared read lock.
            self.session.rollback()
            yield snapshot

    @contextmanager
    def operation_start_guard(
        self,
        required_mode: PortalContour,
    ) -> Iterator[RuntimeModeSnapshot]:
        """Serialize a mode-bound operation start with runtime mode switching."""

        with self.mode_guard(required_mode) as snapshot:
            yield snapshot

    def begin_switch(self, target: PortalContour) -> RuntimeModeSwitchPreparation:
        global _PENDING_SWITCH_TARGET, _PENDING_SWITCH_TOKEN

        with _MODE_LOCK:
            self._require_no_pending_switch()
            previous = self.current_snapshot()
            if target is previous.mode:
                self.session.rollback()
                return RuntimeModeSwitchPreparation(
                    token=None,
                    previous=previous,
                    target=target,
                    blocking_operation_ids=(),
                )

            token = secrets.token_hex(16)
            blockers = self._blocking_operation_ids()
            _PENDING_SWITCH_TOKEN = token
            _PENDING_SWITCH_TARGET = target
            self.session.rollback()
            return RuntimeModeSwitchPreparation(
                token=token,
                previous=previous,
                target=target,
                blocking_operation_ids=blockers,
            )

    def complete_switch(
        self,
        preparation: RuntimeModeSwitchPreparation,
        *,
        actor: User,
        cancelled_operation_ids: Sequence[int] = (),
    ) -> RuntimeModeSwitchResult:
        global _PENDING_SWITCH_TARGET, _PENDING_SWITCH_TOKEN

        if preparation.token is None:
            return RuntimeModeSwitchResult(
                previous=preparation.previous.mode,
                current=preparation.previous.mode,
                changed=False,
                mode_version=preparation.previous.version,
            )

        with _MODE_LOCK:
            if (
                _PENDING_SWITCH_TOKEN != preparation.token
                or _PENDING_SWITCH_TARGET is not preparation.target
            ):
                raise RuntimeModeError(
                    "runtime_mode_switch_invalid",
                    "Runtime mode switch reservation больше не актуальна",
                )

            current = self.current_snapshot()
            if current != preparation.previous:
                raise RuntimeModeError(
                    "runtime_mode_switch_stale",
                    "Authoritative runtime mode изменился во время переключения",
                )

            remaining = self._blocking_operation_ids()
            if remaining:
                joined = ", ".join(f"#{operation_id}" for operation_id in remaining[:10])
                raise RuntimeModeError(
                    "runtime_mode_busy",
                    f"Не удалось безопасно остановить незавершённые операции: {joined}",
                )

            next_version = current.version + 1
            cancelled_ids = tuple(sorted(set(cancelled_operation_ids)))
            metadata: dict[str, object] = {
                "previous": current.mode.value,
                "current": preparation.target.value,
                "mode_version": next_version,
            }
            if cancelled_ids:
                metadata["cancelled_operation_ids"] = list(cancelled_ids)

            try:
                self.metadata.set_value(
                    _RUNTIME_MODE_KEY,
                    preparation.target.value,
                    description=_RUNTIME_MODE_DESCRIPTION,
                )
                self.metadata.set_value(
                    _RUNTIME_MODE_VERSION_KEY,
                    str(next_version),
                    description=_RUNTIME_MODE_VERSION_DESCRIPTION,
                )
                AuditEventRepository(self.session).create(
                    actor=actor,
                    event_type="runtime_mode_changed",
                    metadata=metadata,
                )
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

            self.settings.portal_contour = preparation.target
            _PENDING_SWITCH_TOKEN = None
            _PENDING_SWITCH_TARGET = None
            return RuntimeModeSwitchResult(
                previous=current.mode,
                current=preparation.target,
                changed=True,
                mode_version=next_version,
                cancelled_operation_ids=cancelled_ids,
            )

    def abort_switch(self, token: str | None) -> None:
        global _PENDING_SWITCH_TARGET, _PENDING_SWITCH_TOKEN

        if token is None:
            return
        with _MODE_LOCK:
            if _PENDING_SWITCH_TOKEN == token:
                _PENDING_SWITCH_TOKEN = None
                _PENDING_SWITCH_TARGET = None
            self.session.rollback()

    def switch(self, target: PortalContour, *, actor: User) -> RuntimeModeSwitchResult:
        with _MODE_LOCK:
            self._require_no_pending_switch()
            previous = self.current_snapshot()
            if target is previous.mode:
                return RuntimeModeSwitchResult(
                    previous=previous.mode,
                    current=previous.mode,
                    changed=False,
                    mode_version=previous.version,
                )
            if self._has_blocking_operations():
                raise RuntimeModeError(
                    "runtime_mode_busy",
                    "Нельзя переключить режим, пока выполняется export/import операция",
                )

            next_version = previous.version + 1
            try:
                self.metadata.set_value(
                    _RUNTIME_MODE_KEY,
                    target.value,
                    description=_RUNTIME_MODE_DESCRIPTION,
                )
                self.metadata.set_value(
                    _RUNTIME_MODE_VERSION_KEY,
                    str(next_version),
                    description=_RUNTIME_MODE_VERSION_DESCRIPTION,
                )
                AuditEventRepository(self.session).create(
                    actor=actor,
                    event_type="runtime_mode_changed",
                    metadata={
                        "previous": previous.mode.value,
                        "current": target.value,
                        "mode_version": next_version,
                    },
                )
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

            self.settings.portal_contour = target
            return RuntimeModeSwitchResult(
                previous=previous.mode,
                current=target,
                changed=True,
                mode_version=next_version,
            )

    def _blocking_operation_ids(self) -> tuple[int, ...]:
        return tuple(
            self.session.scalars(
                select(Operation.id)
                .where(Operation.status.in_(BLOCKING_OPERATION_STATUSES))
                .order_by(Operation.id)
            )
        )

    def _has_blocking_operations(self) -> bool:
        count = self.session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(Operation.status.in_(BLOCKING_OPERATION_STATUSES))
        )
        return bool(count)

    @staticmethod
    def _require_no_pending_switch() -> None:
        if _PENDING_SWITCH_TOKEN is not None:
            target = _PENDING_SWITCH_TARGET.value if _PENDING_SWITCH_TARGET is not None else "—"
            raise RuntimeModeError(
                "runtime_mode_switch_in_progress",
                f"Переключение Portal в режим {target} уже выполняется",
            )

    @staticmethod
    def _parse_mode(value: str) -> PortalContour:
        try:
            return PortalContour(value)
        except ValueError as exc:
            raise RuntimeModeError(
                "runtime_mode_invalid",
                "Persisted runtime mode имеет недопустимое значение",
            ) from exc

    @staticmethod
    def _parse_version(value: str) -> int:
        try:
            version = int(value)
        except ValueError as exc:
            raise RuntimeModeError(
                "runtime_mode_invalid",
                "Persisted runtime mode version имеет недопустимое значение",
            ) from exc
        if version < 1:
            raise RuntimeModeError(
                "runtime_mode_invalid",
                "Persisted runtime mode version должен быть положительным",
            )
        return version
