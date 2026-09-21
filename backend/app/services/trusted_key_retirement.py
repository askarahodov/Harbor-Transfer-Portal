from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import PortalContour, Settings
from app.db.models import Operation
from app.domain.bundle import OperationStatus, OperationType
from app.services.key_management import KeyManagementError, KeyManagementService


@dataclass(slots=True)
class TrustedKeyRetirementError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class TrustedKeyRetirementImpact:
    fingerprint: str
    enabled: bool
    enabled_key_count: int
    historical_import_count: int
    blocking_operation_ids: tuple[int, ...]

    @property
    def can_retire(self) -> bool:
        return not self.blocking_operation_ids


_TERMINAL_IMPORT_STATUSES = (
    OperationStatus.COMPLETED,
    OperationStatus.FAILED,
    OperationStatus.REJECTED,
    OperationStatus.CANCELLED,
)


class TrustedKeyRetirementService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.keys = KeyManagementService(settings)

    def impact(self, fingerprint: str) -> TrustedKeyRetirementImpact:
        if self.settings.portal_contour is not PortalContour.TARGET:
            raise TrustedKeyRetirementError(
                "key_management_wrong_contour",
                "Trusted SOURCE public keys можно retire только в контуре TARGET",
            )
        normalized = self.keys._validate_fingerprint(fingerprint)
        try:
            trusted = self.keys.list_trusted_keys()
        except KeyManagementError as exc:
            raise TrustedKeyRetirementError(exc.code, exc.message) from exc
        state = next((item for item in trusted if item.fingerprint == normalized), None)
        if state is None:
            raise TrustedKeyRetirementError(
                "trusted_key_not_found",
                "Trusted public key не найден",
            )

        historical = self.session.scalar(
            select(func.count(Operation.id)).where(
                Operation.type == OperationType.IMPORT,
                Operation.bundle_signing_key_fingerprint == normalized,
            )
        )
        blocking = tuple(
            self.session.scalars(
                select(Operation.id)
                .where(
                    Operation.type == OperationType.IMPORT,
                    Operation.status.not_in(_TERMINAL_IMPORT_STATUSES),
                    or_(
                        Operation.bundle_signing_key_fingerprint == normalized,
                        Operation.bundle_signing_key_fingerprint.is_(None),
                    ),
                )
                .order_by(Operation.id)
            )
        )
        return TrustedKeyRetirementImpact(
            fingerprint=normalized,
            enabled=state.enabled,
            enabled_key_count=sum(1 for item in trusted if item.enabled),
            historical_import_count=int(historical or 0),
            blocking_operation_ids=blocking,
        )

    def require_retirable(self, fingerprint: str) -> TrustedKeyRetirementImpact:
        impact = self.impact(fingerprint)
        if impact.blocking_operation_ids:
            ids = ", ".join(str(item) for item in impact.blocking_operation_ids)
            raise TrustedKeyRetirementError(
                "trusted_key_retirement_blocked",
                f"Trusted key нельзя retire во время READY/in-flight import operations: {ids}",
            )
        return impact
