from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db.models import UserRole
from app.db.repositories import OperationRepository, UserRepository
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType


def migrate_database(path: Path) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")


def test_empty_database_migrates_and_persists(tmp_path: Path) -> None:
    database = tmp_path / "portal.db"
    migrate_database(database)
    engine = create_db_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    assert {"users", "operations", "artifact_results", "setting_metadata"} <= tables

    sessions = create_session_factory(engine)
    with sessions.begin() as session:
        user = UserRepository(session).create(
            username=" Operator ", password_hash="argon2$hash", role=UserRole.OPERATOR
        )
        operation = OperationRepository(session).create(
            operation_type=OperationType.EXPORT,
            status=OperationStatus.CREATED,
            actor=user,
            actor_username=user.username,
        )
        OperationRepository(session).add_artifact(
            operation,
            artifact_type="container-image",
            repository="project/app",
            reference="1.0.0",
            status=ArtifactStatus.PENDING,
        )
        operation_id = operation.id

    with sessions() as session:
        restored = OperationRepository(session).get(operation_id)
        assert restored is not None
        assert restored.actor_username == "operator"
        assert len(restored.artifacts) == 1


def test_username_is_unique_after_normalization(tmp_path: Path) -> None:
    database = tmp_path / "portal.db"
    migrate_database(database)
    sessions = create_session_factory(create_db_engine(f"sqlite:///{database}"))
    with sessions() as session:
        UserRepository(session).create(
            username="Admin",
            password_hash="hash",
            role=UserRole.ADMIN,
        )
        session.commit()

        with pytest.raises(IntegrityError):
            UserRepository(session).create(
                username=" admin ",
                password_hash="hash2",
                role=UserRole.ADMIN,
            )
        session.rollback()


def test_user_model_has_hash_only_not_plaintext_password() -> None:
    from app.db.models import User

    columns = set(User.__table__.columns.keys())
    assert "password_hash" in columns
    assert "password" not in columns
