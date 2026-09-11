from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth.rate_limit import LoginRateLimiter, LoginThrottleScope
from app.db.base import Base
from app.db.models import LoginThrottle


def _limiter(session: Session, **overrides: int) -> LoginRateLimiter:
    values = {
        "window_seconds": 60,
        "username_max_failures": 2,
        "address_max_failures": 4,
        "lockout_seconds": 120,
    }
    values.update(overrides)
    return LoginRateLimiter(session, secret="rate-limit-test-secret", **values)


def test_username_threshold_is_normalized_and_recovers_after_window() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)

    with Session(engine) as session:
        limiter = _limiter(session)
        first = limiter.register_failure(username=" Admin ", client_address=None, now=now)
        assert not first.blocked
        decision = limiter.register_failure(username="admin", client_address=None, now=now).blocked
        assert decision
        assert limiter.check(username="ADMIN", client_address=None, now=now).blocked
        assert not limiter.check(
            username="admin",
            client_address=None,
            now=now + timedelta(seconds=121),
        ).blocked
        assert not limiter.register_failure(
            username="admin",
            client_address=None,
            now=now + timedelta(seconds=121),
        ).blocked


def test_address_limit_is_independent_from_username_limit() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)

    with Session(engine) as session:
        limiter = _limiter(session, username_max_failures=10, address_max_failures=2)
        limiter.register_failure(username="one", client_address="198.51.100.10", now=now)
        decision = limiter.register_failure(username="two", client_address="198.51.100.10", now=now)
        assert decision.blocked
        assert decision.scope is LoginThrottleScope.ADDRESS
        assert limiter.check(
            username="three",
            client_address="198.51.100.10",
            now=now,
        ).blocked
        assert not limiter.check(
            username="three",
            client_address="198.51.100.11",
            now=now,
        ).blocked


def test_success_clears_only_username_counter_and_subjects_are_not_stored_raw() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)

    with Session(engine) as session:
        limiter = _limiter(session, username_max_failures=3, address_max_failures=3)
        limiter.register_failure(username="SensitiveUser", client_address="203.0.113.5", now=now)
        limiter.register_success(username="sensitiveuser")
        rows = list(session.scalars(select(LoginThrottle)))
        assert len(rows) == 1
        assert rows[0].scope == "address"
        assert rows[0].subject_hash not in {"sensitiveuser", "203.0.113.5"}
        assert len(rows[0].subject_hash) == 64
