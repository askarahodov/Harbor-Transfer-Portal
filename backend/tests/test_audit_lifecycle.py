import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.api.imports import get_import_orchestrator
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import AuditEventRepository, OperationRepository, UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.main import create_app

JWT_SECRET = "audit-lifecycle-test-jwt-secret-1234567890"


def _app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'audit-lifecycle.db'}"
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
        users = UserRepository(session)
        admin = users.create(
            username="admin",
            password_hash=hash_password("admin-password-123"),
            role=UserRole.ADMIN,
        )
        operator = users.create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
        return app, admin.id, operator.id


def _login(client: TestClient, username: str, password: str | None = None) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password or f"{username}-password-123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_failure_audit_does_not_store_attempted_identity_or_password(tmp_path: Path) -> None:
    app, _admin_id, _operator_id = _app(tmp_path)
    attempted_username = "secret-looking-nonexistent-user"
    attempted_password = "Never-Store-This-Password-987"

    with TestClient(app) as client:
        failed = client.post(
            "/api/auth/login",
            json={"username": attempted_username, "password": attempted_password},
        )
        assert failed.status_code == 401
        admin_token = _login(client, "admin")
        response = client.get(
            "/api/audit/events",
            headers=_auth(admin_token),
            params={"event_type": "auth.login.failed"},
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    event = response.json()["items"][0]
    assert event["actor_username"] == "system"
    assert event["result"] == "failure"
    assert event["metadata"]["reason"] == "invalid_credentials"
    serialized = response.text
    assert attempted_username not in serialized
    assert attempted_password not in serialized


def test_operation_status_changes_create_system_audit_without_error_message(tmp_path: Path) -> None:
    app, _admin_id, operator_id = _app(tmp_path)
    with app.state.session_factory() as session:
        operator = UserRepository(session).get(operator_id)
        assert operator is not None
        repo = OperationRepository(session)
        export = repo.create(
            operation_type=OperationType.EXPORT,
            status=OperationStatus.RUNNING,
            actor=operator,
            actor_username=operator.username,
        )
        export.delivery_id = "DELIVERY-AUDIT-EXPORT"
        imported = repo.create(
            operation_type=OperationType.IMPORT,
            status=OperationStatus.VERIFYING,
            actor=operator,
            actor_username=operator.username,
        )
        imported.source_delivery_id = "DELIVERY-AUDIT-IMPORT"
        session.commit()

        export.status = OperationStatus.COMPLETED
        imported.status = OperationStatus.READY
        session.commit()

        imported.status = OperationStatus.IMPORTING
        session.commit()
        imported.error_code = "synthetic_failure"
        imported.error_message = "password=must-not-enter-audit"
        imported.status = OperationStatus.FAILED
        session.commit()

        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.actor_username == "system")
                .order_by(AuditEvent.id)
            )
        )

    assert [event.event_type for event in events] == [
        "export.completed",
        "import.verified",
        "import.failed",
    ]
    assert events[-1].result == "failure"
    assert "synthetic_failure" in events[-1].metadata_json
    assert "must-not-enter-audit" not in events[-1].metadata_json


def test_cancel_records_actor_request_and_system_terminal_event(tmp_path: Path) -> None:
    app, _admin_id, operator_id = _app(tmp_path)
    with app.state.session_factory() as session:
        operator = UserRepository(session).get(operator_id)
        assert operator is not None
        operation = OperationRepository(session).create(
            operation_type=OperationType.EXPORT,
            status=OperationStatus.CREATED,
            actor=operator,
            actor_username=operator.username,
        )
        operation.delivery_id = "DELIVERY-CANCEL-AUDIT"
        session.commit()
        operation_id = operation.id

    with TestClient(app) as client:
        token = _login(client, "operator")
        response = client.post(
            f"/api/operations/{operation_id}/cancel",
            headers=_auth(token),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"

    with app.state.session_factory() as session:
        rows = list(
            session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type.in_(
                        ["export.cancel.requested", "export.cancelled"]
                    )
                )
                .order_by(AuditEvent.id)
            )
        )
    assert [(row.event_type, row.actor_username) for row in rows] == [
        ("export.cancel.requested", "operator"),
        ("export.cancelled", "system"),
    ]


def test_import_overwrite_approval_is_attributed_to_actor(tmp_path: Path) -> None:
    app, _admin_id, operator_id = _app(tmp_path)
    operation = SimpleNamespace(
        id=41,
        type=OperationType.IMPORT,
        actor_user_id=operator_id,
        actor_username="operator",
        delivery_id=None,
        source_delivery_id="DELIVERY-CONFLICT-AUDIT",
    )

    class _OperationManager:
        def get_operation(self, operation_id: int):
            assert operation_id == 41
            return operation

    class _Orchestrator:
        operation_manager = _OperationManager()

        def preview(self, operation_id: int):
            assert operation_id == 41
            return SimpleNamespace(
                artifacts=[SimpleNamespace(classification=ImportPreviewState.CONFLICT)]
            )

        async def start_import(
            self,
            operation_id: int,
            *,
            actor_username: str,
            overwrite_conflicts: bool,
        ) -> None:
            assert operation_id == 41
            assert actor_username == "operator"
            assert overwrite_conflicts is True

    app.dependency_overrides[get_import_orchestrator] = lambda: _Orchestrator()
    try:
        with TestClient(app) as client:
            token = _login(client, "operator")
            response = client.post(
                "/api/imports/41/execute",
                headers=_auth(token),
                json={"overwrite_conflicts": True},
            )
        assert response.status_code == 202
    finally:
        app.dependency_overrides.clear()

    with app.state.session_factory() as session:
        rows = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.actor_username == "operator")
                .where(AuditEvent.event_type.like("import.%"))
                .order_by(AuditEvent.id)
            )
        )
    assert [row.event_type for row in rows] == [
        "import.started",
        "import.conflict_overwrite.approved",
    ]
    approval = json.loads(rows[-1].metadata_json)
    assert approval["operation_id"] == 41
    assert approval["source_delivery_id"] == "DELIVERY-CONFLICT-AUDIT"
    assert approval["conflict_count"] == 1


def test_audit_repository_redacts_sensitive_keys_and_bounds_metadata(tmp_path: Path) -> None:
    app, admin_id, _operator_id = _app(tmp_path)
    with app.state.session_factory() as session:
        admin = UserRepository(session).get(admin_id)
        assert admin is not None
        repo = AuditEventRepository(session)
        event = repo.create(
            actor=admin,
            event_type="audit.redaction.test",
            metadata={
                "password": "secret-password-value",
                "nested": {"token": "secret-token-value", "safe": "visible"},
            },
        )
        session.commit()
        metadata = json.loads(event.metadata_json)
        assert metadata == {
            "nested": {"safe": "visible", "token": "[REDACTED]"},
            "password": "[REDACTED]",
        }

        with pytest.raises(ValueError, match="audit metadata exceeds"):
            repo.create(
                actor=admin,
                event_type="audit.too-large",
                metadata={"safe_note": "x" * 9000},
            )
