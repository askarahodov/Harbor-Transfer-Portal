from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import UserRole
from app.db.repositories import OperationRepository, UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app

JWT_SECRET = "history-api-test-jwt-secret-1234567890"


def _app_with_history(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'history-api.db'}"
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
        viewer = users.create(
            username="viewer",
            password_hash=hash_password("viewer-password-123"),
            role=UserRole.VIEWER,
        )
        operations = OperationRepository(session)
        baseline = datetime(2026, 9, 14, 5, 0, tzinfo=UTC)

        first = operations.create(
            operation_type=OperationType.EXPORT,
            status=OperationStatus.COMPLETED,
            actor=operator,
            actor_username=operator.username,
            comment="first export",
        )
        first.delivery_id = "DELIVERY-HISTORY-001"
        first.created_at = baseline

        second = operations.create(
            operation_type=OperationType.IMPORT,
            status=OperationStatus.FAILED,
            actor=admin,
            actor_username=admin.username,
            comment="needle conflict import",
        )
        second.delivery_id = "DELIVERY-HISTORY-002"
        second.error_code = "import_failed"
        second.created_at = baseline + timedelta(minutes=1)

        third = operations.create(
            operation_type=OperationType.EXPORT,
            status=OperationStatus.CREATED,
            actor=operator,
            actor_username=operator.username,
            comment="latest export",
        )
        third.delivery_id = "DELIVERY-HISTORY-003"
        third.created_at = baseline + timedelta(minutes=2)
        session.commit()

    return app, viewer.id


def _login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": f"{username}-password-123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_operation_history_is_paginated_and_newest_first(tmp_path: Path) -> None:
    app, _viewer_id = _app_with_history(tmp_path)

    with TestClient(app) as client:
        token = _login(client, "viewer")
        first_page = client.get(
            "/api/operations?limit=2&offset=0",
            headers=_auth(token),
        )
        second_page = client.get(
            "/api/operations?limit=2&offset=2",
            headers=_auth(token),
        )

    assert first_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert [item["delivery_id"] for item in first_page.json()["items"]] == [
        "DELIVERY-HISTORY-003",
        "DELIVERY-HISTORY-002",
    ]
    assert [item["delivery_id"] for item in second_page.json()["items"]] == [
        "DELIVERY-HISTORY-001"
    ]
    assert "artifacts" not in first_page.json()["items"][0]


def test_operation_history_combines_filters_and_search(tmp_path: Path) -> None:
    app, _viewer_id = _app_with_history(tmp_path)

    with TestClient(app) as client:
        token = _login(client, "viewer")
        response = client.get(
            "/api/operations",
            params={
                "type": "IMPORT",
                "status": "FAILED",
                "actor": "admin",
                "search": "needle",
            },
            headers=_auth(token),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["delivery_id"] == "DELIVERY-HISTORY-002"
    assert payload["items"][0]["error_code"] == "import_failed"


def test_operation_history_validates_bounds_and_date_range(tmp_path: Path) -> None:
    app, _viewer_id = _app_with_history(tmp_path)

    with TestClient(app) as client:
        token = _login(client, "viewer")
        too_large = client.get("/api/operations?limit=101", headers=_auth(token))
        reversed_range = client.get(
            "/api/operations",
            params={
                "created_from": "2026-09-14T06:00:00Z",
                "created_to": "2026-09-14T05:00:00Z",
            },
            headers=_auth(token),
        )
        unauthenticated = client.get("/api/operations")

    assert too_large.status_code == 422
    assert reversed_range.status_code == 422
    assert unauthenticated.status_code == 401
