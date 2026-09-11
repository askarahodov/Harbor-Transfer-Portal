from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.config import Settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.main import create_app


def _client_with_users(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "auth-api.db"
    database_url = f"sqlite:///{db_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret="test-jwt-secret-not-for-production",
        )
    )
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        repo.create(
            username="admin",
            password_hash=hash_password("admin-password-123"),
            role=UserRole.ADMIN,
        )
        repo.create(
            username="viewer",
            password_hash=hash_password("viewer-password-123"),
            role=UserRole.VIEWER,
        )
        session.commit()
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_invalid_login_is_generic(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    missing = client.post("/api/auth/login", json={"username": "missing", "password": "whatever"})
    wrong = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_protected_endpoint_requires_authentication(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    assert client.get("/api/auth/me").status_code == 401


def test_viewer_cannot_manage_users_but_admin_can(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    viewer = _login(client, "viewer", "viewer-password-123")
    admin = _login(client, "admin", "admin-password-123")

    assert client.get("/api/users", headers={"Authorization": f"Bearer {viewer}"}).status_code == 403
    response = client.get("/api/users", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200
    assert {user["username"] for user in response.json()} == {"admin", "viewer"}


def test_disabled_user_token_is_rejected(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    token = _login(client, "viewer", "viewer-password-123")

    with client.app.state.session_factory() as session:
        user = UserRepository(session).get_by_username("viewer")
        assert user is not None
        user.is_active = False
        session.commit()

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
