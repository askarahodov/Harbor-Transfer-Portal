from pathlib import Path

import pytest
from alembic.config import Config
from pydantic import ValidationError

from alembic import command
from app.auth.bootstrap import bootstrap_admin
from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.config import Settings
from app.db.models import UserRole
from app.db.session import create_db_engine, create_session_factory


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


def test_bootstrap_admin_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "auth.db"
    database_url = f"sqlite:///{db_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    session_factory = create_session_factory(create_db_engine(database_url))
    with session_factory() as session:
        first, created = bootstrap_admin(
            session,
            username="Admin",
            password="first-secure-password",
        )
        assert created is True
        assert first.role == UserRole.ADMIN
        original_hash = first.password_hash

    with session_factory() as session:
        second, created = bootstrap_admin(
            session,
            username="admin",
            password="different-secure-password",
        )
        assert created is False
        assert second.password_hash == original_hash
