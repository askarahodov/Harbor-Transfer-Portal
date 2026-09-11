from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jwt import InvalidTokenError, decode, encode

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(*, user_id: int, secret: str, lifetime_minutes: int) -> str:
    now = datetime.now(timezone.utc)
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
