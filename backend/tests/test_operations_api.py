from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app
from app.services.operation_manager import OperationArtifactSpec

JWT_SECRET = "operation-api-test-secret-1234567890-abcdef"


def _app_with_operation(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'operation-api.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            operation_workspace_root=tmp_path / "workspaces",
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
        other = users.create(
            username="other",
            password_hash=hash_password("other-password-123"),
            role=UserRole.OPERATOR,
        )
        users.create(
            username="viewer",
            password_hash=hash_password("viewer-password-123"),
            role=UserRole.VIEWER,
        )
        session.commit()
        operation_id = app.state.operation_manager.create_operation(
            operation_type=OperationType.IMPORT,
            initial_status=OperationStatus.UPLOADED,
            actor=operator,
            actor_username=operator.username,
            artifacts=[
                OperationArtifactSpec(
                    artifact_type="container-image",
                    repository="project/app",
                    reference="1.0.0",
                )
            ],
        )
        return app, operation_id, admin.id, operator.id, other.id


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_operation_detail_is_authenticated_and_viewer_read_only(tmp_path: Path) -> None:
    app, operation_id, _admin_id, _operator_id, _other_id = _app_with_operation(tmp_path)
    with TestClient(app) as client:
        assert client.get(f"/api/operations/{operation_id}").status_code == 401
        viewer = _login(client, "viewer", "viewer-password-123")
        response = client.get(f"/api/operations/{operation_id}", headers=_auth(viewer))
        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == operation_id
        assert payload["type"] == "IMPORT"
        assert payload["status"] == "UPLOADED"
        assert payload["progress"]["total"] == 1
        assert payload["progress"]["completed_artifacts"] == 0
        assert len(payload["artifacts"]) == 1
        assert "stdout" not in response.text
        assert "stderr" not in response.text

        cancel = client.post(f"/api/operations/{operation_id}/cancel", headers=_auth(viewer))
        assert cancel.status_code == 403


def test_operator_can_cancel_only_own_operation_and_admin_can_address_any(
    tmp_path: Path,
) -> None:
    app, operation_id, _admin_id, _operator_id, _other_id = _app_with_operation(tmp_path)
    with TestClient(app) as client:
        other = _login(client, "other", "other-password-123")
        denied = client.post(f"/api/operations/{operation_id}/cancel", headers=_auth(other))
        assert denied.status_code == 403

        operator = _login(client, "operator", "operator-password-123")
        own = client.post(f"/api/operations/{operation_id}/cancel", headers=_auth(operator))
        assert own.status_code == 200
        assert own.json()["status"] == "CANCELLED"

        admin = _login(client, "admin", "admin-password-123")
        admin_attempt = client.post(
            f"/api/operations/{operation_id}/cancel",
            headers=_auth(admin),
        )
        assert admin_attempt.status_code == 409


def test_missing_operation_returns_not_found(tmp_path: Path) -> None:
    app, _operation_id, _admin_id, _operator_id, _other_id = _app_with_operation(tmp_path)
    with TestClient(app) as client:
        viewer = _login(client, "viewer", "viewer-password-123")
        response = client.get("/api/operations/999999", headers=_auth(viewer))
        assert response.status_code == 404
