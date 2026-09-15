import csv
import io
import sqlite3
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from pypdf import PdfReader

from alembic import command
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import ArtifactResult, Operation, UserRole
from app.db.repositories import UserRepository
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.main import create_app

JWT_SECRET = "mapped-reporting-test-secret-" + "x" * 32


def _alembic(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _app(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'mapped-reporting.db'}"
    command.upgrade(_alembic(database_url), "head")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            portal_contour=PortalContour.TARGET,
            operation_workspace_root=tmp_path / "work",
            operation_disk_reserve_bytes=0,
        )
    )
    with app.state.session_factory() as session:
        viewer = UserRepository(session).create(
            username="viewer",
            password_hash=hash_password("viewer-password-123"),
            role=UserRole.VIEWER,
        )
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.COMPLETED,
            actor_username="operator",
            total_artifacts=2,
            successful_artifacts=1,
            skipped_artifacts=1,
            source_delivery_id="DELIVERY-MAPPED-001",
            bundle_sha256="a" * 64,
        )
        session.add(operation)
        session.flush()
        mapped = ArtifactResult(
            operation_id=operation.id,
            artifact_type="container-image",
            repository="source-team/apps/api",
            reference="1.4.2",
            source_digest="sha256:" + "b" * 64,
            target_digest="sha256:" + "b" * 64,
            source_project="source-team",
            source_repository="source-team/apps/api",
            source_reference="1.4.2",
            target_project="docker-prod",
            target_repository="docker-prod/apps/api",
            target_reference="harbor-target.local/docker-prod/apps/api:1.4.2",
            destination_plan_id="plan-" + "c" * 32,
            destination_plan_hash="d" * 64,
            overwrite_approved=False,
            status=ArtifactStatus.VERIFIED,
        )
        legacy = ArtifactResult(
            operation_id=operation.id,
            artifact_type="helm-chart",
            repository="legacy/charts",
            name="demo",
            version="2.0.0",
            source_digest="sha256:" + "e" * 64,
            target_digest="sha256:" + "e" * 64,
            status=ArtifactStatus.SKIPPED,
        )
        session.add_all([mapped, legacy])
        session.commit()
        return app, viewer.id, operation.id


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "viewer-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_migration_adds_nullable_mapping_snapshot_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic(database_url)
    command.upgrade(config, "0007_runtime_mode_operation_snapshot")

    with sqlite3.connect(database_path) as connection:
        before = {row[1] for row in connection.execute("PRAGMA table_info(artifact_results)")}
    assert "target_reference" not in before

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        after = {row[1] for row in connection.execute("PRAGMA table_info(artifact_results)")}
    assert {
        "source_project",
        "source_repository",
        "source_reference",
        "source_version",
        "target_project",
        "target_repository",
        "target_reference",
        "target_version",
        "destination_plan_id",
        "destination_plan_hash",
        "overwrite_approved",
    }.issubset(after)


def test_history_api_exposes_persisted_mapping_and_legacy_nulls(tmp_path: Path) -> None:
    app, _viewer_id, operation_id = _app(tmp_path)

    with TestClient(app) as client:
        response = client.get(f"/api/operations/{operation_id}", headers=_login(client))

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    mapped, legacy = artifacts
    assert mapped["source_project"] == "source-team"
    assert mapped["source_repository"] == "source-team/apps/api"
    assert mapped["source_reference"] == "1.4.2"
    assert mapped["target_project"] == "docker-prod"
    assert mapped["target_repository"] == "docker-prod/apps/api"
    assert mapped["target_reference"] == "harbor-target.local/docker-prod/apps/api:1.4.2"
    assert mapped["target_version"] is None
    assert mapped["destination_plan_hash"] == "d" * 64
    assert mapped["overwrite_approved"] is False
    assert legacy["source_repository"] is None
    assert legacy["target_reference"] is None
    assert legacy["target_version"] is None
    assert legacy["destination_plan_id"] is None


def test_csv_and_pdf_use_persisted_source_target_snapshot(tmp_path: Path) -> None:
    app, _viewer_id, operation_id = _app(tmp_path)

    with TestClient(app) as client:
        headers = _login(client)
        csv_response = client.get(
            f"/api/operations/{operation_id}/report.csv",
            headers=headers,
        )
        pdf_response = client.get(
            f"/api/operations/{operation_id}/report.pdf",
            headers=headers,
        )

    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert rows[0]["source_project"] == "source-team"
    assert rows[0]["source_repository"] == "source-team/apps/api"
    assert rows[0]["target_project"] == "docker-prod"
    assert rows[0]["target_repository"] == "docker-prod/apps/api"
    assert rows[0]["target_reference"] == "harbor-target.local/docker-prod/apps/api:1.4.2"
    assert rows[0]["destination_plan_hash"] == "d" * 64
    assert rows[0]["overwrite_approved"] == "false"
    assert rows[1]["target_reference"] == ""

    assert pdf_response.status_code == 200
    reader = PdfReader(io.BytesIO(pdf_response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    compact_text = "".join(text.split())
    assert "source-team/apps/api:1.4.2" in compact_text
    assert "harbor-target.local/docker-prod/apps/api:1.4.2" in compact_text
    assert "legacy/charts/demo:2.0.0" in compact_text


def test_mapping_csv_fields_keep_formula_injection_protection(tmp_path: Path) -> None:
    app, _viewer_id, operation_id = _app(tmp_path)
    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        mapped = sorted(operation.artifacts, key=lambda item: item.id)[0]
        mapped.target_reference = '=HYPERLINK("https://example.invalid")'
        session.commit()

    with TestClient(app) as client:
        response = client.get(
            f"/api/operations/{operation_id}/report.csv",
            headers=_login(client),
        )

    assert response.status_code == 200
    first = next(csv.DictReader(io.StringIO(response.text)))
    assert first["target_reference"].startswith("'=")
