import json
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app

JWT_SECRET = "test-runtime-mode-jwt-secret-123456789"


def _migrate(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _settings(database_url: str, contour: PortalContour) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
        portal_contour=contour,
    )


def _seed_users(app) -> None:
    with app.state.session_factory() as session:
        repository = UserRepository(session)
        if repository.get_by_username("operator") is None:
            repository.create(
                username="operator",
                password_hash=hash_password("operator-password-123"),
                role=UserRole.OPERATOR,
            )
            repository.create(
                username="viewer",
                password_hash=hash_password("viewer-password-123"),
                role=UserRole.VIEWER,
            )
            session.commit()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _export_selection() -> dict[str, object]:
    return {
        "artifacts": [
            {
                "kind": "container-image",
                "project": "team",
                "repository": "apps/demo",
                "reference": "1.0.0",
                "digest": "sha256:" + "a" * 64,
            }
        ]
    }


def test_runtime_mode_switch_is_immediate_and_persists_across_restart(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'runtime-mode.db'}"
    _migrate(database_url)

    app = create_app(_settings(database_url, PortalContour.SOURCE))
    _seed_users(app)
    with TestClient(app) as client:
        operator = _login(client, "operator", "operator-password-123")
        initial = client.get("/api/runtime", headers=operator)
        assert initial.status_code == 200
        assert initial.json()["mode"] == "SOURCE"
        assert client.get("/api/health").json()["contour"] == "SOURCE"

        switched = client.put(
            "/api/runtime/mode",
            headers=operator,
            json={"mode": "TARGET"},
        )
        assert switched.status_code == 200
        assert switched.json() == {
            "previous": "SOURCE",
            "current": "TARGET",
            "changed": True,
            "cancelled_operation_ids": [],
        }
        assert client.get("/api/health").json()["contour"] == "TARGET"

        export_preview = client.post(
            "/api/exports/preview",
            headers=operator,
            json=_export_selection(),
        )
        assert export_preview.status_code == 409
        assert export_preview.json()["error"]["code"] == "export_wrong_contour"

    restarted = create_app(_settings(database_url, PortalContour.SOURCE))
    with TestClient(restarted) as client:
        operator = _login(client, "operator", "operator-password-123")
        assert client.get("/api/runtime", headers=operator).json()["mode"] == "TARGET"
        assert client.get("/api/health").json()["contour"] == "TARGET"


def test_viewer_can_read_but_cannot_switch_runtime_mode(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'runtime-viewer.db'}"
    _migrate(database_url)
    app = create_app(_settings(database_url, PortalContour.SOURCE))
    _seed_users(app)

    with TestClient(app) as client:
        viewer = _login(client, "viewer", "viewer-password-123")
        assert client.get("/api/runtime", headers=viewer).status_code == 200
        denied = client.put(
            "/api/runtime/mode",
            headers=viewer,
            json={"mode": "TARGET"},
        )
        assert denied.status_code == 403
        assert client.get("/api/health").json()["contour"] == "SOURCE"


def test_runtime_mode_switch_cancels_blocking_operation(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'runtime-cancel.db'}"
    _migrate(database_url)
    app = create_app(_settings(database_url, PortalContour.SOURCE))
    _seed_users(app)

    with TestClient(app) as client:
        operator = _login(client, "operator", "operator-password-123")
        with app.state.session_factory() as session:
            operation = Operation(
                type=OperationType.EXPORT,
                status=OperationStatus.RUNNING,
                actor_username="operator",
                runtime_mode="SOURCE",
                runtime_mode_version=1,
            )
            session.add(operation)
            session.commit()
            operation_id = operation.id

        response = client.put(
            "/api/runtime/mode",
            headers=operator,
            json={"mode": "TARGET"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "previous": "SOURCE",
            "current": "TARGET",
            "changed": True,
            "cancelled_operation_ids": [operation_id],
        }
        assert client.get("/api/health").json()["contour"] == "TARGET"

        with app.state.session_factory() as session:
            cancelled = session.get(Operation, operation_id)
            assert cancelled is not None
            assert cancelled.status is OperationStatus.CANCELLED
            assert cancelled.error_code == "operation_cancelled"
            events = list(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.event_type == "runtime_mode_changed")
                )
            )
        assert len(events) == 1
        assert json.loads(events[0].metadata_json)["cancelled_operation_ids"] == [operation_id]


def test_runtime_mode_switch_writes_audit_event_and_noop_is_idempotent(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'runtime-audit.db'}"
    _migrate(database_url)
    app = create_app(_settings(database_url, PortalContour.SOURCE))
    _seed_users(app)

    with TestClient(app) as client:
        operator = _login(client, "operator", "operator-password-123")
        noop = client.put(
            "/api/runtime/mode",
            headers=operator,
            json={"mode": "SOURCE"},
        )
        assert noop.status_code == 200
        assert noop.json()["changed"] is False

        changed = client.put(
            "/api/runtime/mode",
            headers=operator,
            json={"mode": "TARGET"},
        )
        assert changed.status_code == 200

        with app.state.session_factory() as session:
            events = list(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.event_type == "runtime_mode_changed")
                )
            )
        assert len(events) == 1
        assert events[0].actor_username == "operator"
        assert json.loads(events[0].metadata_json) == {
            "current": "TARGET",
            "mode_version": 2,
            "previous": "SOURCE",
        }
