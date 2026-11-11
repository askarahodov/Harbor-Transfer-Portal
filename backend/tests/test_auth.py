import pytest
from pydantic import ValidationError

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.config import Settings


def test_password_is_hashed_and_verified() -> None:
    password = "a-strong-password-123"
    password_hash = hash_password(password)
    assert password not in password_hash
    assert verify_password(password, password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_access_token_round_trip_and_invalid_signature() -> None:
    token = create_access_token(user_id=42, secret="secret-one", lifetime_minutes=30)
    assert decode_access_token(token, "secret-one") == 42
    with pytest.raises(ValueError):
        decode_access_token(token, "secret-two")


def test_settings_reject_weak_or_placeholder_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret="short")
    with pytest.raises(ValidationError):
        Settings(jwt_secret="replace-with-random-high-entropy-secret")
