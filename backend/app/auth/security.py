from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jwt import InvalidTokenError, decode, encode

_password_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = _password_hasher.hash("dummy-login-password-never-used")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return _password_hasher.hash(password)


def hash_initial_admin_password() -> str:
    """Only for the hard-coded first-run account; normal passwords require >=12 chars."""
    return _password_hasher.hash("admin")


def read_access_token_version(token: str, secret: str) -> int:
    try:
        payload = decode(token, secret, algorithms=["HS256"])
        version = payload.get("ver", 0)  # pre-migration JWTs are version zero
        if payload.get("type") != "access" or type(version) is not int or version < 0:
            raise ValueError("invalid token version")
        return version
    except (InvalidTokenError, TypeError, ValueError) as exc:
        raise ValueError("invalid token version") from exc


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_login_password(password: str, password_hash: str | None) -> bool:
    return verify_password(password, password_hash or _DUMMY_PASSWORD_HASH)


def create_access_token(
    *, user_id: int, secret: str, lifetime_minutes: int, token_version: int = 0
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
        "last_activity": now.isoformat(),
        "type": "access",
        "ver": token_version,
    }
    return encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> int:
    try:
        payload = decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise ValueError("invalid token type")
        return int(payload["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid access token") from exc


def check_and_refresh_token(
    token: str,
    secret: str,
    inactivity_timeout_minutes: int,
    lifetime_minutes: int,
) -> tuple[int, str | None]:
    """Validate the signed JWT and its idle window, without silently extending it.

    Background polling and API requests must not count as human activity.
    Only the explicit authenticated /auth/activity endpoint renews the session.
    The legacy tuple return shape is kept for existing auth dependencies.
    """
    try:
        payload = decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise ValueError("invalid token type")
        user_id = int(payload["sub"])
        last_activity_raw = payload["last_activity"]
        if not isinstance(last_activity_raw, str):
            raise ValueError("invalid last_activity")
        last_activity = datetime.fromisoformat(last_activity_raw)
        if last_activity.tzinfo is None:
            raise ValueError("last_activity must be timezone-aware")
        now = datetime.now(UTC)
        idle_duration = now - last_activity
        if idle_duration < timedelta(0) or idle_duration >= timedelta(
            minutes=inactivity_timeout_minutes
        ):
            raise ValueError("session expired by inactivity")
        return user_id, None
    except (InvalidTokenError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid access token") from exc


def refresh_access_token(
    token: str,
    secret: str,
    lifetime_minutes: int,
    inactivity_timeout_minutes: int = 60,
    token_version: int = 0,
) -> str:
    """Renew only a valid active JWT, signed with the configured secret."""
    user_id, _ = check_and_refresh_token(
        token,
        secret,
        inactivity_timeout_minutes=inactivity_timeout_minutes,
        lifetime_minutes=lifetime_minutes,
    )
    return create_access_token(
        user_id=user_id,
        secret=secret,
        lifetime_minutes=max(lifetime_minutes, inactivity_timeout_minutes),
        token_version=token_version,
    )

def create_export_download_token(
    *,
    user_id: int,
    operation_id: int,
    secret: str,
    lifetime_seconds: int,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "operation_id": operation_id,
        "iat": now,
        "exp": now + timedelta(seconds=lifetime_seconds),
        "type": "export-download",
    }
    return encode(payload, secret, algorithm="HS256")


def decode_export_download_token(
    token: str,
    secret: str,
    *,
    expected_operation_id: int,
) -> int:
    try:
        payload = decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "export-download":
            raise ValueError("invalid token type")
        if int(payload["operation_id"]) != expected_operation_id:
            raise ValueError("download token operation mismatch")
        return int(payload["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid export download token") from exc
