import hashlib
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app
from app.services.operation_manager import OperationArtifactSpec

JWT_SECRET = "export-api-test-jwt-secret-1234567890"
DELIVERY_ID = "DELIVERY-20260911-EXPORTAPITEST"


def _app_with_users(
    tmp_path: Path,
    *,
    contour: PortalContour = PortalContour.SOURCE,
):
    database_url = f"sqlite:///{tmp_path / 'export-api.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(
        Settings(
            _env_file=None,
            portal_contour=contour,
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            operation_workspace_root=tmp_path / "work",
            operation_disk_reserve_bytes=0,
            bundle_outgoing_root=tmp_path / "outgoing",
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


def _completed_export(app, actor_user_id: int) -> int:
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=actor_user_id,
        actor_username="operator",
        delivery_id=DELIVERY_ID,
        artifacts=[
            OperationArtifactSpec(
                artifact_type="container-image",
                repository="project/app",
                reference="1.0",
                source_digest="sha256:" + "1" * 64,
            )
        ],
    )
    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.status = OperationStatus.COMPLETED
        session.commit()

    outgoing = app.state.settings.bundle_outgoing_root
    outgoing.mkdir(parents=True, exist_ok=True)
    archive = outgoing / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = outgoing / f"{archive.name}.sha256"
    payload = b"streamed-export-bundle"
    archive.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar.write_text(f"{digest}  {archive.name}\n")
    return operation_id


def test_target_contour_and_viewer_are_rejected_before_export_validation(
    tmp_path: Path,
) -> None:
    app, _ids = _app_with_users(tmp_path, contour=PortalContour.TARGET)
    payload = {
        "artifacts": [
            {
                "type": "container-image",
                "project": "project",
                "repository": "app",
                "reference": "1.0",
            }
        ]
    }
    with TestClient(app) as client:
        operator = _login(client, "operator")
        response = client.post(
            "/api/exports/validate",
            headers=_auth(operator),
            json=payload,
        )
        viewer = _login(client, "viewer")
        viewer_response = client.post(
            "/api/exports/validate",
            headers=_auth(viewer),
            json=payload,
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "export_wrong_contour"
    assert viewer_response.status_code == 403


def test_completed_bundle_metadata_and_streaming_download_are_authorized(
    tmp_path: Path,
) -> None:
    app, ids = _app_with_users(tmp_path)
    operation_id = _completed_export(app, ids["operator"])
    expected = hashlib.sha256(b"streamed-export-bundle").hexdigest()

    with TestClient(app) as client:
        creator = _login(client, "operator")
        metadata = client.get(
            f"/api/exports/{operation_id}/bundle",
            headers=_auth(creator),
        )
        download = client.get(
            f"/api/exports/{operation_id}/bundle/download",
            headers=_auth(creator),
        )
        checksum = client.get(
            f"/api/exports/{operation_id}/bundle/checksum",
            headers=_auth(creator),
        )

        other = _login(client, "other")
        denied = client.get(
            f"/api/exports/{operation_id}/bundle",
            headers=_auth(other),
        )
        viewer = _login(client, "viewer")
        viewer_denied = client.get(
            f"/api/exports/{operation_id}/bundle",
            headers=_auth(viewer),
        )
        admin = _login(client, "admin")
        admin_response = client.get(
            f"/api/exports/{operation_id}/bundle",
            headers=_auth(admin),
        )

    assert metadata.status_code == 200
    assert metadata.json()["delivery_id"] == DELIVERY_ID
    assert metadata.json()["sha256"] == expected
    assert metadata.json()["size_bytes"] == len(b"streamed-export-bundle")
    assert download.status_code == 200
    assert download.content == b"streamed-export-bundle"
    assert download.headers["content-length"] == str(len(download.content))
    assert checksum.status_code == 200
    assert checksum.text == f"{expected}  {DELIVERY_ID}.htp.tar.gz\n"
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "export_bundle_forbidden"
    assert viewer_denied.status_code == 403
    assert admin_response.status_code == 200


def test_bundle_metadata_requires_completed_operation(tmp_path: Path) -> None:
    app, ids = _app_with_users(tmp_path)
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=ids["operator"],
        actor_username="operator",
        delivery_id=DELIVERY_ID,
    )
    with TestClient(app) as client:
        token = _login(client, "operator")
        response = client.get(
            f"/api/exports/{operation_id}/bundle",
            headers=_auth(token),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "export_bundle_not_ready"
