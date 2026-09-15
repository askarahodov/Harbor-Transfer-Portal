from __future__ import annotations

from collections.abc import Iterator
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


class RuntimeModeService:
    """Persistent authoritative runtime mode and its operation-start barrier.

    PORTAL_CONTOUR remains the bootstrap default only. Once the persistent setting exists,
    it wins on every application startup. The same process-wide lock serializes mode
    switches with mode-bound operation creation for the v1 single-backend-instance model.
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
    def operation_start_guard(
        self,
        required_mode: PortalContour,
    ) -> Iterator[RuntimeModeSnapshot]:
        """Serialize a mode-bound operation start with runtime mode switching."""

        with _MODE_LOCK:
            snapshot = self.current_snapshot()
            if snapshot.mode is not required_mode:
                raise RuntimeModeError(
                    "runtime_mode_mismatch",
                    f"Операция требует режим {required_mode.value}, текущий режим {snapshot.mode.value}",
                )
            yield snapshot

    def switch(self, target: PortalContour, *, actor: User) -> RuntimeModeSwitchResult:
        with _MODE_LOCK:
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

    def _has_blocking_operations(self) -> bool:
        count = self.session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(Operation.status.in_(BLOCKING_OPERATION_STATUSES))
        )
        return bool(count)

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
