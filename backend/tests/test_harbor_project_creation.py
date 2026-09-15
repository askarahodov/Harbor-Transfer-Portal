from __future__ import annotations

import json
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.api.harbor import get_harbor_client
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app
from app.services.harbor_client import HarborClientError, HarborPage, HarborProject

JWT_SECRET = "project-create-test-jwt-secret-123456789"


class FakeProjectHarborClient:
    base_url = "https://harbor.target.local"

    def __init__(self) -> None:
        self.projects: dict[str, bool] = {}
        self.create_calls: list[tuple[str, bool]] = []
        self.create_error: HarborClientError | None = None
        self.conflict_after_create = False

    def list_projects_page(
        self,
        page: int,
        page_size: int,
        *,
        search_needle: str | None = None,
    ) -> HarborPage[HarborProject]:
        items = [
            HarborProject(project_id=index, name=name, public=public)
            for index, (name, public) in enumerate(sorted(self.projects.items()), start=1)
            if search_needle is None or search_needle.casefold() in name.casefold()
        ]
        start = (page - 1) * page_size
        return HarborPage(items=tuple(items[start : start + page_size]), total=len(items))

    def create_project(self, name: str, *, public: bool = False) -> None:
        self.create_calls.append((name, public))
        if self.conflict_after_create:
            self.projects[name] = public
            raise HarborClientError("conflict", "raw duplicate response", 409)
        if self.create_error is not None:
            raise self.create_error
        self.projects[name] = public


def _environment(
    tmp_path: Path,
    *,
    contour: PortalContour = PortalContour.TARGET,
) -> tuple[object, TestClient, FakeProjectHarborClient, dict[str, str], int]:
    database_url = f"sqlite:///{tmp_path / 'project-create.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            portal_contour=contour,
            harbor_url="https://harbor.target.local",
        )
    )
    with app.state.session_factory() as session:
        users = UserRepository(session)
        for role in (UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER):
            users.create(
                username=role.value,
                password_hash=hash_password(f"{role.value}-password-123"),
                role=role,
            )
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    harbor = FakeProjectHarborClient()
    app.dependency_overrides[get_harbor_client] = lambda: harbor
    client = TestClient(app)
    tokens: dict[str, str] = {}
    for role in ("admin", "operator", "viewer"):
        login = client.post(
            "/api/auth/login",
            json={"username": role, "password": f"{role}-password-123"},
        )
        assert login.status_code == 200
        tokens[role] = login.json()["access_token"]
    return app, client, harbor, tokens, operation_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_only_admin_can_create_project_and_import_stays_ready(tmp_path: Path) -> None:
    app, client, harbor, tokens, operation_id = _environment(tmp_path)
    payload = {"name": "docker-prod", "operation_id": operation_id}

    for role in ("viewer", "operator"):
        denied = client.post(
            "/api/harbor/projects",
            json=payload,
            headers=_auth(tokens[role]),
        )
        assert denied.status_code == 403
    assert harbor.create_calls == []

    created = client.post(
        "/api/harbor/projects",
        json=payload,
        headers=_auth(tokens["admin"]),
    )
    assert created.status_code == 200
    assert created.json() == {"name": "docker-prod", "public": False, "created": True}
    assert harbor.create_calls == [("docker-prod", False)]

    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.READY
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "harbor.project.create")
        )
        assert event is not None
        assert event.actor_username == "admin"
        assert event.result == "created"
        metadata = json.loads(event.metadata_json)
        assert metadata == {
            "local_harbor": "harbor.target.local",
            "operation_id": operation_id,
            "project": "docker-prod",
            "public": False,
        }


def test_existing_and_concurrent_duplicate_create_are_idempotent(tmp_path: Path) -> None:
    _app, client, harbor, tokens, operation_id = _environment(tmp_path)
    harbor.projects["helm-prod"] = False

    existing = client.post(
        "/api/harbor/projects",
        json={"name": "helm-prod", "operation_id": operation_id},
        headers=_auth(tokens["admin"]),
    )
    assert existing.status_code == 200
    assert existing.json()["created"] is False
    assert harbor.create_calls == []

    harbor.conflict_after_create = True
    raced = client.post(
        "/api/harbor/projects",
        json={"name": "race-project", "operation_id": operation_id},
        headers=_auth(tokens["admin"]),
    )
    assert raced.status_code == 200
    assert raced.json() == {"name": "race-project", "public": False, "created": False}
    assert harbor.create_calls == [("race-project", False)]


def test_project_create_rejects_path_host_and_credentials_fields(tmp_path: Path) -> None:
    _app, client, harbor, tokens, _operation_id = _environment(tmp_path)
    invalid_payloads = [
        {"name": "../docker-prod"},
        {"name": "https://evil.invalid/project"},
        {"name": "Docker-Prod"},
        {"name": "docker-prod", "registry_url": "https://evil.invalid"},
        {"name": "docker-prod", "username": "attacker", "password": "secret"},
    ]
    for payload in invalid_payloads:
        response = client.post(
            "/api/harbor/projects",
            json=payload,
            headers=_auth(tokens["admin"]),
        )
        assert response.status_code == 422
    assert harbor.create_calls == []


def test_harbor_permission_failure_is_safe_and_audited(tmp_path: Path) -> None:
    app, client, harbor, tokens, operation_id = _environment(tmp_path)
    harbor.create_error = HarborClientError(
        "forbidden",
        "password=must-not-leak",
        403,
    )

    response = client.post(
        "/api/harbor/projects",
        json={"name": "docker-prod", "operation_id": operation_id},
        headers=_auth(tokens["admin"]),
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "harbor_forbidden"
    assert "must-not-leak" not in response.text

    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None and operation.status is OperationStatus.READY
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "harbor.project.create")
        )
        assert event is not None
        assert event.result == "failure"
        assert json.loads(event.metadata_json)["error_code"] == "forbidden"
        assert "password" not in event.metadata_json.casefold()
        assert "must-not-leak" not in event.metadata_json


def test_project_create_requires_target_runtime_mode(tmp_path: Path) -> None:
    _app, client, harbor, tokens, _operation_id = _environment(
        tmp_path,
        contour=PortalContour.SOURCE,
    )
    response = client.post(
        "/api/harbor/projects",
        json={"name": "docker-prod"},
        headers=_auth(tokens["admin"]),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runtime_mode_mismatch"
    assert harbor.create_calls == []
