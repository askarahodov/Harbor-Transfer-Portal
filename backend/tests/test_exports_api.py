from pathlib import Path

from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app

JWT_SECRET = "exports-api-test-jwt-secret-1234567890"
DIGEST = "sha256:" + "a" * 64
DELIVERY_ID = "DELIVERY-20260911-API12345"


def _app_with_users(tmp_path: Path, contour: PortalContour):
    database_url = f"sqlite:///{tmp_path / 'exports-api.db'}"
    settings = Settings(
        _env_file=None,
        portal_contour=contour,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
        harbor_url="https://harbor.local",
        operation_workspace_root=tmp_path / "data" / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        bundle_outgoing_root=tmp_path / "data" / "outgoing",
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.db_engine)
    user_ids: dict[str, int] = {}
    with app.state.session_factory() as session:
        repo = UserRepository(session)
        for username, role in (
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


def _selection_payload() -> dict[str, object]:
    return {
        "artifacts": [
            {
                "kind": "container-image",
                "project": "project-a",
                "repository": "apps/demo",
                "reference": "1.0.0",
                "digest": DIGEST,
            }
        ],
        "comment": "release candidate",
    }


def test_viewer_cannot_preview_or_start_export(tmp_path: Path) -> None:
    app, _user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        preview = client.post(
            "/api/exports/preview",
            headers=_auth(viewer),
            json=_selection_payload(),
        )
        start = client.post(
            "/api/exports",
            headers=_auth(viewer),
            json=_selection_payload(),
        )

    assert preview.status_code == 403
    assert start.status_code == 403


def test_target_contour_rejects_export_with_stable_error_code(tmp_path: Path) -> None:
    app, _user_ids = _app_with_users(tmp_path, PortalContour.TARGET)
    with TestClient(app) as client:
        operator = _login(client, "operator")
        response = client.post(
            "/api/exports/preview",
            headers=_auth(operator),
            json=_selection_payload(),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "export_wrong_contour"


def test_download_is_owner_scoped_and_exposes_length_and_sha256(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    archive = app.state.settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = archive.with_name(archive.name + ".sha256")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"large-bundle-stream-path")

    import hashlib

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    with app.state.session_factory() as session:
        operation = Operation(
            delivery_id=DELIVERY_ID,
            type=OperationType.EXPORT,
            status=OperationStatus.COMPLETED,
            actor_user_id=user_ids["operator"],
            actor_username="operator",
            bundle_filename=archive.name,
            bundle_sha256=digest,
            bundle_size_bytes=archive.stat().st_size,
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    with TestClient(app) as client:
        owner = _login(client, "operator")
        other = _login(client, "other")
        forbidden = client.get(
            f"/api/exports/{operation_id}/download",
            headers=_auth(other),
        )
        response = client.get(
            f"/api/exports/{operation_id}/download",
            headers=_auth(owner),
        )

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.headers["content-length"] == str(archive.stat().st_size)
    assert response.headers["x-checksum-sha256"] == digest
    assert response.content == b"large-bundle-stream-path"


def test_generic_operation_status_projects_persisted_bundle_metadata(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    with app.state.session_factory() as session:
        operation = Operation(
            delivery_id=DELIVERY_ID,
            type=OperationType.EXPORT,
            status=OperationStatus.COMPLETED,
            actor_user_id=user_ids["operator"],
            actor_username="operator",
            bundle_filename=f"{DELIVERY_ID}.htp.tar.gz",
            bundle_sha256="f" * 64,
            bundle_size_bytes=987654321,
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        response = client.get(
            f"/api/operations/{operation_id}",
            headers=_auth(viewer),
        )

    assert response.status_code == 200
    assert response.json()["bundle"] == {
        "filename": f"{DELIVERY_ID}.htp.tar.gz",
        "size_bytes": 987654321,
        "sha256": "f" * 64,
    }
