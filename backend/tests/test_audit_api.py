from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_SECRET = "audit-api-test-jwt-secret-1234567890"


def _app_with_users(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'audit-api.db'}"
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
        repo = UserRepository(session)
        for username, role in (
            ("admin", UserRole.ADMIN),
            ("operator", UserRole.OPERATOR),
            ("viewer", UserRole.VIEWER),
        ):
            repo.create(
                username=username,
                password_hash=hash_password(f"{username}-password-123"),
                role=role,
            )
        session.commit()
    return app


def _login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": f"{username}-password-123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_success_and_failure_create_secret_free_audit_events(tmp_path: Path) -> None:
    app = _app_with_users(tmp_path)
    wrong_password = "definitely-not-the-real-password"

    with TestClient(app) as client:
        failed = client.post(
            "/api/auth/login",
            json={"username": "operator", "password": wrong_password},
        )
        assert failed.status_code == 401
        admin = _login(client, "admin")
        response = client.get(
            "/api/audit/events",
            headers=_auth(admin),
            params={"limit": 20},
        )

    assert response.status_code == 200
    events = response.json()["items"]
    failed_event = next(item for item in events if item["event_type"] == "auth.login.failed")
    success_event = next(item for item in events if item["event_type"] == "auth.login.succeeded")
    assert failed_event["actor_username"] == "operator"
    assert failed_event["result"] == "failure"
    assert failed_event["metadata"] == {"reason": "invalid_credentials"}
    assert success_event["actor_username"] == "admin"
    assert success_event["metadata"] == {"role": "admin"}
    serialized = response.text
    assert wrong_password not in serialized
    assert "access_token" not in serialized
    assert JWT_SECRET not in serialized


def test_user_mutations_create_secret_free_audit_events(tmp_path: Path) -> None:
    app = _app_with_users(tmp_path)
    initial_password = "Initial-Secret-Password-123"
    rotated_password = "Rotated-Secret-Password-456"

    with TestClient(app) as client:
        admin = _login(client, "admin")
        created = client.post(
            "/api/users",
            headers=_auth(admin),
            json={
                "username": "audited-user",
                "password": initial_password,
                "role": "operator",
            },
        )
        assert created.status_code == 201
        target_user_id = created.json()["id"]

        updated = client.patch(
            f"/api/users/{target_user_id}",
            headers=_auth(admin),
            json={"role": "viewer", "password": rotated_password},
        )
        assert updated.status_code == 200

        response = client.get(
            "/api/audit/events",
            headers=_auth(admin),
            params={"actor": "admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    user_events = [item for item in payload["items"] if item["event_type"].startswith("user.")]
    assert [item["event_type"] for item in user_events] == [
        "user.updated",
        "user.created",
    ]
    assert user_events[0]["metadata"] == {
        "target_user_id": target_user_id,
        "target_username": "audited-user",
        "changed_fields": ["password", "role"],
    }
    serialized = response.text
    assert initial_password not in serialized
    assert rotated_password not in serialized
    assert "$argon2" not in serialized


def test_audit_history_is_admin_only_and_filterable(tmp_path: Path) -> None:
    app = _app_with_users(tmp_path)

    with TestClient(app) as client:
        admin = _login(client, "admin")
        operator = _login(client, "operator")
        viewer = _login(client, "viewer")
        created = client.post(
            "/api/users",
            headers=_auth(admin),
            json={
                "username": "filter-target",
                "password": "filter-target-password-123",
                "role": "operator",
            },
        )
        assert created.status_code == 201

        filtered = client.get(
            "/api/audit/events",
            headers=_auth(admin),
            params={"event_type": "user.created", "result": "success", "limit": 1},
        )
        operator_response = client.get("/api/audit/events", headers=_auth(operator))
        viewer_response = client.get("/api/audit/events", headers=_auth(viewer))
        unauthenticated = client.get("/api/audit/events")
        too_large = client.get("/api/audit/events?limit=101", headers=_auth(admin))

    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert len(filtered.json()["items"]) == 1
    assert operator_response.status_code == 403
    assert viewer_response.status_code == 403
    assert unauthenticated.status_code == 401
    assert too_large.status_code == 422
