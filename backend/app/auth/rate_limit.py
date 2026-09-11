from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import LoginThrottle


class LoginThrottleScope(StrEnum):
    USERNAME = "username"
    ADDRESS = "address"


@dataclass(frozen=True, slots=True)
class LoginRateLimitDecision:
    blocked: bool
    retry_after_seconds: int = 0
    scope: LoginThrottleScope | None = None
    subject_fingerprint: str | None = None


class LoginRateLimiter:
    def __init__(
        self,
        session: Session,
        *,
        secret: str,
        window_seconds: int,
        username_max_failures: int,
        address_max_failures: int,
        lockout_seconds: int,
    ) -> None:
        if not secret:
            raise ValueError("rate-limit secret must not be empty")
        if min(window_seconds, username_max_failures, address_max_failures, lockout_seconds) < 1:
            raise ValueError("rate-limit settings must be positive")
        self.session = session
        self._secret = secret.encode("utf-8")
        self.window = timedelta(seconds=window_seconds)
        self.lockout = timedelta(seconds=lockout_seconds)
        self.thresholds = {
            LoginThrottleScope.USERNAME: username_max_failures,
            LoginThrottleScope.ADDRESS: address_max_failures,
        }

    def check(
        self,
        *,
        username: str,
        client_address: str | None,
        now: datetime | None = None,
    ) -> LoginRateLimitDecision:
        current = self._normalize_now(now)
        decisions = [
            self._decision_for(scope, subject, current)
            for scope, subject in self._subjects(username, client_address)
        ]
        blocked = [decision for decision in decisions if decision.blocked]
        if not blocked:
            return LoginRateLimitDecision(blocked=False)
        return max(blocked, key=lambda decision: decision.retry_after_seconds)

    def register_failure(
        self,
        *,
        username: str,
        client_address: str | None,
        now: datetime | None = None,
    ) -> LoginRateLimitDecision:
        current = self._normalize_now(now)
        decisions = [
            self._record_failure(scope, subject, current)
            for scope, subject in self._subjects(username, client_address)
        ]
        self.session.flush()
        blocked = [decision for decision in decisions if decision.blocked]
        if not blocked:
            return LoginRateLimitDecision(blocked=False)
        return max(blocked, key=lambda decision: decision.retry_after_seconds)

    def register_success(self, *, username: str) -> None:
        normalized = self._normalize_username(username)
        fingerprint = self._fingerprint(LoginThrottleScope.USERNAME, normalized)
        self.session.execute(
            delete(LoginThrottle).where(
                LoginThrottle.scope == LoginThrottleScope.USERNAME.value,
                LoginThrottle.subject_hash == fingerprint,
            )
        )

    def _decision_for(
        self,
        scope: LoginThrottleScope,
        subject: str,
        now: datetime,
    ) -> LoginRateLimitDecision:
        fingerprint = self._fingerprint(scope, subject)
        row = self._get(scope, fingerprint)
        if row is None or row.locked_until is None:
            return LoginRateLimitDecision(blocked=False)
        locked_until = self._as_utc(row.locked_until)
        if locked_until <= now:
            return LoginRateLimitDecision(blocked=False)
        return LoginRateLimitDecision(
            blocked=True,
            retry_after_seconds=max(1, math.ceil((locked_until - now).total_seconds())),
            scope=scope,
            subject_fingerprint=fingerprint,
        )

    def _record_failure(
        self,
        scope: LoginThrottleScope,
        subject: str,
        now: datetime,
    ) -> LoginRateLimitDecision:
        fingerprint = self._fingerprint(scope, subject)
        row = self._get(scope, fingerprint)
        if row is None:
            row = LoginThrottle(
                scope=scope.value,
                subject_hash=fingerprint,
                failure_count=0,
                window_started_at=now,
            )
            self.session.add(row)

        if row.locked_until is not None:
            locked_until = self._as_utc(row.locked_until)
            if locked_until > now:
                return LoginRateLimitDecision(
                    blocked=True,
                    retry_after_seconds=max(1, math.ceil((locked_until - now).total_seconds())),
                    scope=scope,
                    subject_fingerprint=fingerprint,
                )

        window_started_at = self._as_utc(row.window_started_at)
        if now - window_started_at >= self.window:
            row.failure_count = 0
            row.window_started_at = now
            row.locked_until = None
        elif row.locked_until is not None and self._as_utc(row.locked_until) <= now:
            row.locked_until = None

        row.failure_count += 1
        if row.failure_count < self.thresholds[scope]:
            return LoginRateLimitDecision(blocked=False)

        row.locked_until = now + self.lockout
        return LoginRateLimitDecision(
            blocked=True,
            retry_after_seconds=max(1, math.ceil(self.lockout.total_seconds())),
            scope=scope,
            subject_fingerprint=fingerprint,
        )

    def _get(self, scope: LoginThrottleScope, fingerprint: str) -> LoginThrottle | None:
        return self.session.scalar(
            select(LoginThrottle).where(
                LoginThrottle.scope == scope.value,
                LoginThrottle.subject_hash == fingerprint,
            )
        )

    def _subjects(
        self,
        username: str,
        client_address: str | None,
    ) -> list[tuple[LoginThrottleScope, str]]:
        subjects = [(LoginThrottleScope.USERNAME, self._normalize_username(username))]
        normalized_address = self._normalize_address(client_address)
        if normalized_address is not None:
            subjects.append((LoginThrottleScope.ADDRESS, normalized_address))
        return subjects

    def _fingerprint(self, scope: LoginThrottleScope, subject: str) -> str:
        message = f"{scope.value}\0{subject}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().lower()

    @staticmethod
    def _normalize_address(client_address: str | None) -> str | None:
        if client_address is None:
            return None
        normalized = client_address.strip().lower()
        if not normalized or len(normalized) > 255:
            return None
        return normalized

    @staticmethod
    def _normalize_now(now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        return LoginRateLimiter._as_utc(value)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
