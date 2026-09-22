import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.config import BrowserScheme, PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import OperationStatus, OperationType
from app.main import create_app

JWT_SECRET = "exports-api-test-jwt-secret-1234567890"
DIGEST = "sha256:" + "a" * 64
DELIVERY_ID = "DELIVERY-20260911-API12345"


def _app_with_users(
    tmp_path: Path,
    contour: PortalContour,
    *,
    browser_scheme: BrowserScheme = BrowserScheme.HTTP,
):
    database_url = f"sqlite:///{tmp_path / 'exports-api.db'}"
    settings = Settings(
        _env_file=None,
        portal_contour=contour,
        portal_browser_scheme=browser_scheme,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
        harbor_url="https://harbor.local",
        operation_workspace_root=tmp_path / "data" / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        bundle_outgoing_root=tmp_path / "data" / "outgoing",
        bundle_signing_private_key_file=(
            tmp_path / "data" / "keys" / "source-signing-private.pem"
        ),
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


def _cookie_attributes(response) -> set[str]:
    return {
        part.strip().lower()
        for part in response.headers["set-cookie"].split(";")[1:]
    }


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


def _completed_export(app, user_id: int) -> tuple[int, Path, str]:
    archive = app.state.settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = archive.with_name(archive.name + ".sha256")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"large-bundle-stream-path")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    with app.state.session_factory() as session:
        operation = Operation(
            delivery_id=DELIVERY_ID,
            type=OperationType.EXPORT,
            status=OperationStatus.COMPLETED,
            actor_user_id=user_id,
            actor_username="operator",
            bundle_filename=archive.name,
            bundle_sha256=digest,
            bundle_size_bytes=archive.stat().st_size,
        )
        session.add(operation)
        session.commit()
        return operation.id, archive, digest


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


def test_start_export_requires_signing_identity_before_operation_creation(
    tmp_path: Path,
) -> None:
    app, _user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)

    with TestClient(app) as client:
        operator = _login(client, "operator")
        response = client.post(
            "/api/exports",
            headers=_auth(operator),
            json=_selection_payload(),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "bundle_signing_key_not_configured"
    with app.state.session_factory() as session:
        assert session.query(Operation).count() == 0


def test_export_selection_rejects_duplicate_reference_and_invalid_image_tag(
    tmp_path: Path,
) -> None:
    app, _user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    duplicate = _selection_payload()
    first = duplicate["artifacts"][0]  # type: ignore[index]
    duplicate["artifacts"] = [  # type: ignore[index]
        first,
        {**first, "digest": "sha256:" + "b" * 64},  # type: ignore[arg-type]
    ]
    invalid = _selection_payload()
    invalid["artifacts"][0]["reference"] = "bad/tag"  # type: ignore[index]

    with TestClient(app) as client:
        operator = _login(client, "operator")
        duplicate_response = client.post(
            "/api/exports/preview",
            headers=_auth(operator),
            json=duplicate,
        )
        invalid_response = client.post(
            "/api/exports/preview",
            headers=_auth(operator),
            json=invalid,
        )

    assert duplicate_response.status_code == 422
    assert invalid_response.status_code == 422


def test_download_is_owner_scoped_and_exposes_length_and_sha256(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    operation_id, archive, digest = _completed_export(app, user_ids["operator"])

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
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"large-bundle-stream-path"


def test_download_ticket_allows_native_browser_stream_without_bearer(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    operation_id, archive, digest = _completed_export(app, user_ids["operator"])

    with TestClient(app) as client:
        owner = _login(client, "operator")
        ticket = client.post(
            f"/api/exports/{operation_id}/download-ticket",
            headers=_auth(owner),
        )
        assert ticket.status_code == 200
        assert ticket.json() == {
            "download_url": f"/api/exports/{operation_id}/download",
            "expires_in_seconds": 120,
        }
        attributes = _cookie_attributes(ticket)
        assert "httponly" in attributes
        assert "samesite=strict" in attributes
        assert f"path=/api/exports/{operation_id}/download" in attributes
        assert "secure" not in attributes

        response = client.get(f"/api/exports/{operation_id}/download")

    assert response.status_code == 200
    assert response.headers["content-length"] == str(archive.stat().st_size)
    assert response.headers["x-checksum-sha256"] == digest
    assert response.content == b"large-bundle-stream-path"


def test_download_ticket_ignores_spoofed_https_forwarding_header(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(
        tmp_path,
        PortalContour.SOURCE,
        browser_scheme=BrowserScheme.HTTP,
    )
    operation_id, _archive, _digest = _completed_export(app, user_ids["operator"])

    with TestClient(app) as client:
        owner = _login(client, "operator")
        response = client.post(
            f"/api/exports/{operation_id}/download-ticket",
            headers={**_auth(owner), "X-Forwarded-Proto": "https"},
        )

    assert response.status_code == 200
    assert "secure" not in _cookie_attributes(response)


def test_download_ticket_stays_secure_when_https_is_configured(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(
        tmp_path,
        PortalContour.SOURCE,
        browser_scheme=BrowserScheme.HTTPS,
    )
    operation_id, _archive, _digest = _completed_export(app, user_ids["operator"])

    with TestClient(app) as client:
        owner = _login(client, "operator")
        response = client.post(
            f"/api/exports/{operation_id}/download-ticket",
            headers={**_auth(owner), "X-Forwarded-Proto": "http"},
        )

    assert response.status_code == 200
    assert "secure" in _cookie_attributes(response)


def test_download_ticket_is_owner_scoped_and_viewer_cannot_mint_it(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path, PortalContour.SOURCE)
    operation_id, _archive, _digest = _completed_export(app, user_ids["operator"])

    with TestClient(app) as client:
        other = _login(client, "other")
        viewer = _login(client, "viewer")
        other_response = client.post(
            f"/api/exports/{operation_id}/download-ticket",
            headers=_auth(other),
        )
        viewer_response = client.post(
            f"/api/exports/{operation_id}/download-ticket",
            headers=_auth(viewer),
        )
        unauthenticated = client.get(f"/api/exports/{operation_id}/download")

    assert other_response.status_code == 403
    assert viewer_response.status_code == 403
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "download_auth_required"


def test_expired_completed_bundle_returns_gone_and_keeps_history(tmp_path: Path) -> None:
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
            bundle_size_bytes=123,
            finished_at=datetime.now(UTC) - timedelta(days=8),
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    with TestClient(app) as client:
        operator = _login(client, "operator")
        response = client.get(
            f"/api/exports/{operation_id}/bundle",
            headers=_auth(operator),
        )
        history = client.get(
            f"/api/operations/{operation_id}",
            headers=_auth(operator),
        )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "export_bundle_expired"
    assert history.status_code == 200
    assert history.json()["bundle"]["sha256"] == "f" * 64


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
