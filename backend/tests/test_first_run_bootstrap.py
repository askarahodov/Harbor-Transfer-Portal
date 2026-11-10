from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import AuditEvent, UserRole
from app.db.repositories import UserRepository
from app.main import create_app

SECRET = "first-run-tests-jwt-secret-1234567890123456789"


def _app(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'bootstrap.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(Settings(_env_file=None, database_url=url, jwt_secret=SECRET))


def test_first_run_requires_password_change_and_revokes_old_tokens(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert response.status_code == 200
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).json()["must_change_password"] is True
        assert client.get("/api/users", headers=headers).status_code == 403
        assert client.get("/api/users", headers=headers).json()["error"]["message"] == "password_change_required"
        assert client.get("/api/settings", headers=headers).status_code == 403
        assert client.post("/api/auth/activity", headers=headers).status_code == 204
        assert client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "wrong",
            "new_password": "new-admin-password-123",
        }).status_code == 400
        assert client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin", "new_password": "short",
        }).status_code == 422
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin", "new_password": "new-admin-password-123",
        })
        assert changed.status_code == 204
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert client.post("/api/auth/login", json={
            "username": "admin", "password": "admin",
        }).status_code == 401
        new_login = client.post("/api/auth/login", json={
            "username": "admin", "password": "new-admin-password-123",
        })
        assert new_login.status_code == 200
        new_headers = {"Authorization": f"Bearer {new_login.json()['access_token']}"}
        assert client.get("/api/auth/me", headers=new_headers).json()["must_change_password"] is False
        assert client.get("/api/users", headers=new_headers).status_code == 200
        with app.state.session_factory() as session:
            events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
            assert any(event.event_type == "auth.bootstrap.created" for event in events)
            assert any(event.event_type == "auth.password.changed" for event in events)
            assert "new-admin-password-123" not in "".join(event.metadata_json for event in events)

    with TestClient(_app(tmp_path)) as restarted:
        assert restarted.post("/api/auth/login", json={
            "username": "admin", "password": "admin",
        }).status_code == 401
        assert restarted.post("/api/auth/login", json={
            "username": "admin", "password": "new-admin-password-123",
        }).status_code == 200


def test_existing_user_prevents_default_admin_creation(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with app.state.session_factory() as session:
        UserRepository(session).create(
            username="operator", password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={
            "username": "admin", "password": "admin",
        }).status_code == 401
        with app.state.session_factory() as session:
            assert UserRepository(session).get_by_username("admin") is None
