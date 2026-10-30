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


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_login_password(password: str, password_hash: str | None) -> bool:
    return verify_password(password, password_hash or _DUMMY_PASSWORD_HASH)


def create_access_token(*, user_id: int, secret: str, lifetime_minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
        "last_activity": now.isoformat(),
        "type": "access",
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
    """
    Decode token, check inactivity timeout, and optionally refresh.
    
    Returns:
        tuple[user_id, new_token | None]
        
    If token is valid and not expired by inactivity, returns (user_id, None).
    If token is valid but inactive for too long, raises ValueError.
    If token is valid and should be refreshed (approaching inactivity timeout),
        returns (user_id, new_token).
    """
    try:
        payload = decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise ValueError("invalid token type")
        
        user_id = int(payload["sub"])
        
        # Check inactivity timeout
        last_activity_str = payload.get("last_activity")
        if last_activity_str is None:
            # Legacy token without last_activity - treat as expired
            raise ValueError("token missing last_activity")
        
        last_activity = datetime.fromisoformat(last_activity_str)
        now = datetime.now(UTC)
        inactive_duration = now - last_activity
        
        if inactive_duration > timedelta(minutes=60):  # hard limit: 1 hour absolute max
            raise ValueError("token expired by absolute timeout")
        
        inactivity_timeout = timedelta(minutes=60)  # 1 hour inactivity timeout
        if inactive_duration > inactivity_timeout:
            raise ValueError("token expired by inactivity timeout")
        
        # Check if we should refresh (e.g., within 10 minutes of inactivity timeout)
        refresh_threshold = timedelta(minutes=10)
        if (inactivity_timeout - inactive_duration) < refresh_threshold:
            # Refresh the token
            new_token = create_access_token(
                user_id=int(payload["sub"]),
                secret="",  # will be replaced by caller
                lifetime_minutes=30,
            )
            return int(payload["sub"]), new_token
        
        return int(payload["sub"]), None
        
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid access token") from exc


def refresh_access_token(token: str, secret: str, lifetime_minutes: int) -> str:
    """Refresh an existing valid token, preserving user_id and updating last_activity."""
    try:
        payload = decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise ValueError("invalid token type")
        
        user_id = int(payload["sub"])
        return create_access_token(user_id=user_id, secret=secret, lifetime_minutes=30)
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid access token") from exc


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
