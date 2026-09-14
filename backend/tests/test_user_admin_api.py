import json
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

JWT_SECRET = "user-admin-test-jwt-secret-1234567890"


def _client(tmp_path: Path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'users.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(Settings(_env_file=None, database_url=database_url, jwt_secret=JWT_SECRET))
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        repo.create(
            username="admin",
            password_hash=hash_password("admin-password-123"),
            role=UserRole.ADMIN,
        )
        repo.create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_user_list_contains_operational_metadata_without_secrets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin = _login(client, "admin", "admin-password-123")

    response = client.get("/api/users", headers=_auth(admin))

    assert response.status_code == 200
    users = response.json()
    assert {item["username"] for item in users} == {"admin", "operator"}
    admin_item = next(item for item in users if item["username"] == "admin")
    assert admin_item["created_at"]
    assert admin_item["updated_at"]
    assert admin_item["last_login_at"]
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "hash" not in serialized


def test_last_active_admin_cannot_be_disabled_or_demoted(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    users = client.get("/api/users", headers=_auth(admin)).json()
    admin_id = next(item["id"] for item in users if item["username"] == "admin")

    disabled = client.patch(
        f"/api/users/{admin_id}",
        headers=_auth(admin),
        json={"is_active": False},
    )
    demoted = client.patch(
        f"/api/users/{admin_id}",
        headers=_auth(admin),
        json={"role": "viewer"},
    )

    assert disabled.status_code == demoted.status_code == 409
    assert disabled.json()["error"]["message"] == "at least one active admin is required"


def test_admin_can_manage_user_and_audit_stays_secret_free(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    new_password = "rotated-operator-password-456"
    users = client.get("/api/users", headers=_auth(admin)).json()
    operator_id = next(item["id"] for item in users if item["username"] == "operator")

    updated = client.patch(
        f"/api/users/{operator_id}",
        headers=_auth(admin),
        json={"role": "viewer", "is_active": True, "password": new_password},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "viewer"
    assert "password" not in updated.text.lower()

    old_login = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-password-123"},
    )
    new_login = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": new_password},
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200

    with client.app.state.session_factory() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "user.updated")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        metadata = json.loads(event.metadata_json)
        assert metadata["target_username"] == "operator"
        assert metadata["changed_fields"] == ["password", "role"]
        assert new_password not in event.metadata_json


def test_second_active_admin_allows_first_admin_deactivation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin = _login(client, "admin", "admin-password-123")
    created = client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "username": "backup-admin",
            "password": "backup-admin-password-123",
            "role": "admin",
        },
    )
    assert created.status_code == 201

    users = client.get("/api/users", headers=_auth(admin)).json()
    admin_id = next(item["id"] for item in users if item["username"] == "admin")
    response = client.patch(
        f"/api/users/{admin_id}",
        headers=_auth(admin),
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_operator_cannot_use_user_mutation_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    operator = _login(client, "operator", "operator-password-123")

    response = client.post(
        "/api/users",
        headers=_auth(operator),
        json={
            "username": "viewer-two",
            "password": "viewer-password-123",
            "role": "viewer",
        },
    )
    assert response.status_code == 403
