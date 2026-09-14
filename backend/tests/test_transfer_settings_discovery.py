import asyncio
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from starlette.requests import Request

from alembic import command
from app.api.imports import get_import_orchestrator
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.main import create_app

JWT_TEST_KEY = "transfer-discovery-test-" + "x" * 32
MIB = 1024**2


def _build_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'transfer-discovery.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    app = create_app(
        Settings(
            database_url=database_url,
            jwt_secret=JWT_TEST_KEY,
            portal_contour=PortalContour.TARGET,
            operation_workspace_root=tmp_path / "operations",
            import_discovery_root=tmp_path / "incoming",
            import_staging_root=tmp_path / "staged",
            import_receipt_root=tmp_path / "receipts",
            bundle_extract_root=tmp_path / "verified",
            bundle_temp_root=tmp_path / "bundles",
            bundle_payload_root=tmp_path,
            bundle_trusted_public_keys_dir=tmp_path / "trusted",
        )
    )
    with app.state.session_factory() as session:
        UserRepository(session).create(
            username="admin",
            password_hash=hash_password("admin-test-passphrase"),
            role=UserRole.ADMIN,
        )
        session.commit()
    return app


def test_incoming_discovery_uses_archive_limit_not_browser_upload_limit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-test-passphrase"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    updated = client.patch(
        "/api/settings/transfer",
        json={
            "import_max_upload_bytes": MIB,
            "bundle_max_archive_bytes": 2 * MIB,
            "bundle_max_extracted_bytes": 4 * MIB,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert updated.status_code == 200

    incoming = app.state.settings.import_discovery_root
    incoming.mkdir(parents=True, exist_ok=True)
    archive = incoming / "large-media.htp.tar.gz"
    archive.write_bytes(b"x" * (MIB + 1))
    sidecar = incoming / "large-media.htp.tar.gz.sha256"
    sidecar.write_text("placeholder\n", encoding="utf-8")

    request = Request({"type": "http", "app": app, "headers": []})
    orchestrator = get_import_orchestrator(request)
    monkeypatch.setattr(orchestrator, "_create_intake_operation", lambda **_kwargs: 123)
    monkeypatch.setattr(orchestrator, "_submit_preview", lambda _operation_id: None)

    discovered = asyncio.run(
        orchestrator.discover_ready(
            actor_user_id=1,
            actor_username="admin",
        )
    )

    assert len(discovered) == 1
    assert discovered[0].operation_id == 123
