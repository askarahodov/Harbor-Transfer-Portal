from __future__ import annotations

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
_MODE_LOCK = RLock()

_BLOCKING_OPERATION_STATUSES = {
    OperationStatus.CREATED,
    OperationStatus.VALIDATING,
    OperationStatus.RUNNING,
    OperationStatus.PACKAGING,
    OperationStatus.VERIFYING,
    OperationStatus.UPLOADED,
    OperationStatus.DISCOVERED,
    OperationStatus.IMPORTING,
    OperationStatus.VERIFYING_TARGET,
}


@dataclass(slots=True)
class RuntimeModeError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class RuntimeModeSwitchResult:
    previous: PortalContour
    current: PortalContour
    changed: bool


class RuntimeModeService:
    """Persistent authoritative runtime mode with an in-process compatibility projection.

    PORTAL_CONTOUR remains the bootstrap default only. Once the persistent setting exists,
    it wins on every application startup. The resolved value is projected back into the
    shared Settings instance so existing export/import contour guards switch immediately
    without recreating services or containers.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.metadata = SettingMetadataRepository(session)

    def initialize(self) -> PortalContour:
        with _MODE_LOCK:
            stored = self.metadata.get_value(_RUNTIME_MODE_KEY)
            if stored is None:
                mode = self.settings.portal_contour
                self.metadata.set_value(
                    _RUNTIME_MODE_KEY,
                    mode.value,
                    description=_RUNTIME_MODE_DESCRIPTION,
                )
                self.session.commit()
            else:
                mode = self._parse_mode(stored)
            self.settings.portal_contour = mode
            return mode

    def current(self) -> PortalContour:
        stored = self.metadata.get_value(_RUNTIME_MODE_KEY)
        if stored is None:
            return self.initialize()
        mode = self._parse_mode(stored)
        self.settings.portal_contour = mode
        return mode

    def switch(self, target: PortalContour, *, actor: User) -> RuntimeModeSwitchResult:
        with _MODE_LOCK:
            previous = self.current()
            if target is previous:
                return RuntimeModeSwitchResult(previous=previous, current=previous, changed=False)
            if self._has_blocking_operations():
                raise RuntimeModeError(
                    "runtime_mode_busy",
                    "Нельзя переключить режим, пока выполняется export/import операция",
                )

            try:
                self.metadata.set_value(
                    _RUNTIME_MODE_KEY,
                    target.value,
                    description=_RUNTIME_MODE_DESCRIPTION,
                )
                AuditEventRepository(self.session).create(
                    actor=actor,
                    event_type="runtime_mode_changed",
                    metadata={"previous": previous.value, "current": target.value},
                )
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

            self.settings.portal_contour = target
            return RuntimeModeSwitchResult(previous=previous, current=target, changed=True)

    def _has_blocking_operations(self) -> bool:
        count = self.session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(Operation.status.in_(_BLOCKING_OPERATION_STATUSES))
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
