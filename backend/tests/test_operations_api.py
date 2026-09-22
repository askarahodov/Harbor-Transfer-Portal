from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationType
from app.main import create_app

JWT_SECRET = "operation-api-test-jwt-secret-1234567890"


def _app_with_users(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'operation-api.db'}"
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
    user_ids: dict[str, int] = {}
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        for username, role in (
            ("admin", UserRole.ADMIN),
            ("operator", UserRole.OPERATOR),
            ("other", UserRole.OPERATOR),
            ("viewer", UserRole.VIEWER),
        ):
            user = repo.create(
                username=username,
                password_hash=hash_password(f"{username}-password-123"),
                role=role,
            )
            user_ids[username] = user.id
        session.commit()
    return app, user_ids


def _login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": f"{username}-password-123",
        },
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_operation_status_is_readable_by_authenticated_viewer(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=user_ids["operator"],
        actor_username="operator",
    )

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        response = client.get(f"/api/operations/{operation_id}", headers=_auth(viewer))

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == operation_id
    assert payload["status"] == "CREATED"
    assert payload["progress"] == {
        "total_artifacts": 0,
        "completed_artifacts": 0,
        "running_artifacts": 0,
        "successful_artifacts": 0,
        "failed_artifacts": 0,
        "skipped_artifacts": 0,
        "conflict_artifacts": 0,
        "progress_current": 0,
        "progress_total": 0,
        "current_phase": "CREATED",
        "running_artifact_ids": [],
    }


def test_operation_exposes_safe_harbor_profile_snapshot(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=user_ids["operator"],
        actor_username="operator",
        harbor_profile_id="a" * 32,
        harbor_profile_name="Harbor A",
        harbor_profile_url="https://harbor-a.local",
    )

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        detail = client.get(f"/api/operations/{operation_id}", headers=_auth(viewer))
        listing = client.get("/api/operations", headers=_auth(viewer))

    assert detail.status_code == 200
    assert detail.json()["harbor_profile_id"] == "a" * 32
    assert detail.json()["harbor_profile_name"] == "Harbor A"
    assert detail.json()["harbor_profile_url"] == "https://harbor-a.local"
    listed = next(item for item in listing.json()["items"] if item["id"] == operation_id)
    assert listed["harbor_profile_id"] == "a" * 32
    assert listed["harbor_profile_name"] == "Harbor A"
    assert listed["harbor_profile_url"] == "https://harbor-a.local"


def test_cancel_requires_creator_or_admin_and_reaches_terminal_state(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=user_ids["operator"],
        actor_username="operator",
    )

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        other = _login(client, "other")
        creator = _login(client, "operator")

        assert (
            client.post(
                f"/api/operations/{operation_id}/cancel",
                headers=_auth(viewer),
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/operations/{operation_id}/cancel",
                headers=_auth(other),
            ).status_code
            == 403
        )

        response = client.post(
            f"/api/operations/{operation_id}/cancel",
            headers=_auth(creator),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "CANCELLED"
    assert payload["cancel_requested"] is True
    assert payload["error_code"] == "operation_cancelled"


def test_admin_can_cancel_another_users_operation(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=user_ids["operator"],
        actor_username="operator",
    )

    with TestClient(app) as client:
        admin = _login(client, "admin")
        response = client.post(
            f"/api/operations/{operation_id}/cancel",
            headers=_auth(admin),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_operation_endpoint_requires_authentication_and_handles_missing(tmp_path: Path) -> None:
    app, _user_ids = _app_with_users(tmp_path)

    with TestClient(app) as client:
        assert client.get("/api/operations/999").status_code == 401
        viewer = _login(client, "viewer")
        assert client.get("/api/operations/999", headers=_auth(viewer)).status_code == 404
