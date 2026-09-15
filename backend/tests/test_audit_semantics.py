import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.api.imports import execute_import
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationType
from app.domain.imports import ImportPreviewState
from app.main import create_app
from app.schemas.imports import ImportExecuteRequest

JWT_SECRET = "audit-semantics-test-jwt-secret-1234567890"


def _app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'audit-semantics.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            operation_workspace_root=tmp_path / "work",
            operation_disk_reserve_bytes=0,
        )
    )
    with app.state.session_factory() as session:
        operator = UserRepository(session).create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
        operator_id = operator.id
    return app, operator_id


def test_failed_login_audit_uses_system_actor_and_omits_attempted_identity(tmp_path: Path) -> None:
    app, _operator_id = _app(tmp_path)
    attempted_username = "attacker-controlled-identity"
    attempted_password = "Never-Persist-This-Password-987"

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": attempted_username, "password": attempted_password},
        )
    assert response.status_code == 401

    with app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "auth.login.failed")
        )
        assert event is not None
        assert event.actor_user_id is None
        assert event.actor_username == "system"
        assert json.loads(event.metadata_json)["reason"] == "invalid_credentials"
        serialized = event.metadata_json + event.actor_username

    assert attempted_username not in serialized
    assert attempted_password not in serialized


def test_overwrite_approval_requires_actual_conflict(tmp_path: Path) -> None:
    app, operator_id = _app(tmp_path)

    with app.state.session_factory() as session:
        operator = UserRepository(session).get(operator_id)
        assert operator is not None

        class _OperationManager:
            def __init__(self, operation_id: int) -> None:
                self.operation_id = operation_id

            def get_operation(self, operation_id: int):
                assert operation_id == self.operation_id
                return SimpleNamespace(
                    id=operation_id,
                    type=OperationType.IMPORT,
                    actor_user_id=operator.id,
                    actor_username=operator.username,
                    source_delivery_id=f"DELIVERY-{operation_id}",
                )

        class _Orchestrator:
            def __init__(self, operation_id: int, states: list[ImportPreviewState]) -> None:
                self.operation_manager = _OperationManager(operation_id)
                self.states = states

            def destination_plan(self, operation_id: int):
                assert operation_id == self.operation_manager.operation_id
                return SimpleNamespace(
                    plan_id=f"plan-{operation_id}",
                    plan_hash="b" * 64,
                    bundle_sha256="a" * 64,
                    mapping_policy_revision=0,
                    artifacts=[
                        SimpleNamespace(
                            index=index,
                            artifact_type="container-image",
                            source_repository=f"source/app-{index}",
                            target_repository=f"target/app-{index}",
                            final_reference=f"harbor.target.local/target/app-{index}:1.0",
                            classification=state,
                        )
                        for index, state in enumerate(self.states)
                    ],
                )

            async def start_import(
                self,
                operation_id: int,
                *,
                actor_username: str,
                overwrite_conflicts: bool,
                destination_plan_id: str | None = None,
            ) -> None:
                assert operation_id == self.operation_manager.operation_id
                assert actor_username == operator.username
                assert overwrite_conflicts is True
                assert destination_plan_id is None

        no_conflict = _Orchestrator(41, [ImportPreviewState.NEW, ImportPreviewState.SAME])
        response = asyncio.run(
            execute_import(
                41,
                ImportExecuteRequest(overwrite_conflicts=True),
                operator,
                no_conflict,
                session,
            )
        )
        assert response.operation_id == 41

        with_conflict = _Orchestrator(
            42,
            [ImportPreviewState.NEW, ImportPreviewState.CONFLICT, ImportPreviewState.CONFLICT],
        )
        response = asyncio.run(
            execute_import(
                42,
                ImportExecuteRequest(overwrite_conflicts=True),
                operator,
                with_conflict,
                session,
            )
        )
        assert response.operation_id == 42

        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.like("import.%"))
                .order_by(AuditEvent.id)
            )
        )

    assert [event.event_type for event in events] == [
        "import.started",
        "import.started",
        "import.overwrite.approved",
    ]
    first_metadata = json.loads(events[0].metadata_json)
    second_metadata = json.loads(events[1].metadata_json)
    approval_metadata = json.loads(events[2].metadata_json)
    assert first_metadata["conflict_count"] == 0
    assert first_metadata["destination_plan_id"] == "plan-41"
    assert first_metadata["destination_plan_hash"] == "b" * 64
    assert first_metadata["mapping_policy_revision"] == 0
    assert first_metadata["destination_count"] == 2
    assert first_metadata["destinations_truncated"] is False
    assert first_metadata["destinations"] == [
        {
            "index": 0,
            "artifact_type": "container-image",
            "source_repository": "source/app-0",
            "target_repository": "target/app-0",
            "final_reference": "harbor.target.local/target/app-0:1.0",
        },
        {
            "index": 1,
            "artifact_type": "container-image",
            "source_repository": "source/app-1",
            "target_repository": "target/app-1",
            "final_reference": "harbor.target.local/target/app-1:1.0",
        },
    ]
    assert second_metadata["conflict_count"] == 2
    assert second_metadata["destination_plan_id"] == "plan-42"
    assert second_metadata["destination_plan_hash"] == "b" * 64
    assert approval_metadata["conflict_count"] == 2
    assert approval_metadata["source_delivery_id"] == "DELIVERY-42"
    serialized = json.dumps(
        [first_metadata, second_metadata, approval_metadata],
        sort_keys=True,
    ).lower()
    assert "password" not in serialized
    assert "credential" not in serialized
    assert "private_key" not in serialized
