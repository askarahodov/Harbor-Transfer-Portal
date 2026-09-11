from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import LoginThrottle, UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_SECRET = "test-jwt-secret-not-for-production-123456"


def _client_with_users(tmp_path: Path, **settings_overrides: int) -> TestClient:
    db_path = tmp_path / "auth-api.db"
    database_url = f"sqlite:///{db_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            **settings_overrides,
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


def test_throttled_login_is_generic_persistent_and_recovers(tmp_path: Path) -> None:
    settings = {
        "login_rate_limit_window_seconds": 300,
        "login_rate_limit_username_max_failures": 2,
        "login_rate_limit_address_max_failures": 100,
        "login_rate_limit_lockout_seconds": 60,
    }
    client = _client_with_users(tmp_path, **settings)
    first = client.post("/api/auth/login", json={"username": " Admin ", "password": "wrong"})
    threshold = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-again"},
    )
    blocked = client.post(
        "/api/auth/login",
        json={"username": "ADMIN", "password": "admin-password-123"},
    )
    assert first.status_code == threshold.status_code == blocked.status_code == 401
    assert first.json() == threshold.json() == blocked.json()
    client.close()

    database_url = f"sqlite:///{tmp_path / 'auth-api.db'}"
    restarted_app = create_app(
        Settings(database_url=database_url, jwt_secret=JWT_SECRET, **settings)
    )
    with TestClient(restarted_app) as restarted:
        still_blocked = restarted.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-password-123"},
        )
        assert still_blocked.status_code == 401
        assert still_blocked.json() == first.json()

        now = datetime.now(timezone.utc)
        with restarted.app.state.session_factory() as session:
            throttle = session.scalar(
                select(LoginThrottle).where(LoginThrottle.scope == "username")
            )
            assert throttle is not None
            throttle.locked_until = now - timedelta(seconds=1)
            throttle.window_started_at = now - timedelta(seconds=301)
            session.commit()

        recovered = restarted.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-password-123"},
        )
        assert recovered.status_code == 200


def test_protected_endpoint_requires_authentication(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    assert client.get("/api/auth/me").status_code == 401


def test_viewer_cannot_manage_users_but_admin_can(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    viewer = _login(client, "viewer", "viewer-password-123")
    admin = _login(client, "admin", "admin-password-123")

    viewer_response = client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {viewer}"},
    )
    assert viewer_response.status_code == 403
    response = client.get("/api/users", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200
    assert {user["username"] for user in response.json()} == {"admin", "viewer"}


def test_admin_create_user_rejects_whitespace_username(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    response = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin}"},
        json={"username": "   ", "password": "new-user-password-123", "role": "viewer"},
    )
    assert response.status_code == 422


def test_disabled_user_token_is_rejected(tmp_path: Path) -> None:
    client = _client_with_users(tmp_path)
    token = _login(client, "viewer", "viewer-password-123")

    with client.app.state.session_factory() as session:
        user = UserRepository(session).get_by_username("viewer")
        assert user is not None
        user.is_active = False
        session.commit()

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
