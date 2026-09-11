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
