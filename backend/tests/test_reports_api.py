import csv
import io
import json
from datetime import UTC, datetime
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
from app.schemas.imports import ImportReceiptArtifactResponse, ImportReceiptResponse

JWT_SECRET = "reports-api-test-jwt-secret-1234567890"
SECRET_VALUE = "NeverPersistThisReportSecret987"


def _app_with_users(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'reports-api.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    app = create_app(
        Settings(
            _env_file=None,
            portal_contour=PortalContour.TARGET,
            database_url=database_url,
            jwt_secret=JWT_SECRET,
            operation_workspace_root=tmp_path / "work",
            import_staging_root=tmp_path / "incoming" / "staged",
            import_discovery_root=tmp_path / "incoming",
            import_receipt_root=tmp_path / "receipts",
            bundle_extract_root=tmp_path / "verified",
            operation_disk_reserve_bytes=0,
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
        json={"username": username, "password": f"{username}-password-123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_completed_import(app, operator_id: int) -> int:  # type: ignore[no-untyped-def]
    started = datetime(2026, 9, 14, 6, 30, tzinfo=UTC)
    finished = datetime(2026, 9, 14, 6, 31, tzinfo=UTC)
    delivery_id = "DELIVERY-20260914-REPORT01"
    bundle_sha = "a" * 64
    source_digest = "sha256:" + "b" * 64
    target_digest = "sha256:" + "c" * 64

    with app.state.session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.COMPLETED,
            actor_user_id=operator_id,
            actor_username="operator",
            comment=f"password={SECRET_VALUE}",
            started_at=started,
            finished_at=finished,
            total_artifacts=2,
            successful_artifacts=1,
            skipped_artifacts=0,
            conflict_artifacts=1,
            failed_artifacts=0,
            source_delivery_id=delivery_id,
            bundle_sha256=bundle_sha,
            bundle_signing_key_fingerprint="d" * 64,
            import_preview_json=json.dumps(
                {
                    "source_delivery_id": delivery_id,
                    "source_harbor": "harbor-source.local",
                    "source_portal_version": "0.1.0",
                    "source_created_at": started.isoformat(),
                    "source_created_by": "оператор",
                    "source_comment": "Поставка сервисов",
                    "checksum_verified": True,
                    "signature_verified": True,
                    "schema_verified": True,
                },
                ensure_ascii=False,
            ),
        )
        session.add(operation)
        session.flush()
        operation_id = operation.id
        session.add_all(
            [
                ArtifactResult(
                    operation_id=operation_id,
                    artifact_type="container-image",
                    repository="=HYPERLINK(\"https://example.invalid\")",
                    reference="1.0",
                    source_digest=source_digest,
                    target_digest=target_digest,
                    status=ArtifactStatus.IMPORTED,
                ),
                ArtifactResult(
                    operation_id=operation_id,
                    artifact_type="helm-chart",
                    repository="platform/charts",
                    name="сервис",
                    version="2.0.0",
                    source_digest=source_digest,
                    target_digest="sha256:" + "e" * 64,
                    status=ArtifactStatus.CONFLICT,
                    error_code="artifact_conflict",
                    error_message=f"token={SECRET_VALUE}",
                ),
            ]
        )
        receipt = ImportReceiptResponse(
            operation_id=operation_id,
            source_delivery_id=delivery_id,
            bundle_sha256=bundle_sha,
            actor_username="operator",
            started_at=started,
            finished_at=finished,
            overwrite_conflicts=False,
            result="COMPLETED",
            artifacts=[
                ImportReceiptArtifactResponse(
                    index=0,
                    artifact_type="container-image",
                    repository="platform/app",
                    reference="1.0",
                    expected_digest=source_digest,
                    target_digest=source_digest,
                    status=ArtifactStatus.IMPORTED,
                )
            ],
        )
        operation.import_receipt_json = receipt.model_dump_json()
        session.commit()
        return operation_id


def _create_failed_export(app, operator_id: int) -> int:  # type: ignore[no-untyped-def]
    with app.state.session_factory() as session:
        operation = Operation(
            delivery_id="DELIVERY-20260914-FAILED01",
            type=OperationType.EXPORT,
            status=OperationStatus.FAILED,
            actor_user_id=operator_id,
            actor_username="operator",
            error_code="export_failed",
            error_message=f"secret={SECRET_VALUE}",
            total_artifacts=0,
            failed_artifacts=0,
        )
        session.add(operation)
        session.commit()
        return operation.id


def test_csv_report_is_stable_formula_safe_and_secret_free(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = _create_completed_import(app, user_ids["operator"])

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        response = client.get(
            f"/api/operations/{operation_id}/report.csv",
            headers=_auth(viewer),
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="operation-{operation_id}.csv"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert SECRET_VALUE not in response.text

    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    assert reader.fieldnames is not None
    assert reader.fieldnames[:8] == [
        "operation_id",
        "delivery_id",
        "source_delivery_id",
        "operation_type",
        "operation_status",
        "actor",
        "started_at",
        "finished_at",
    ]
    assert len(rows) == 2
    assert rows[0]["delivery_id"] == "DELIVERY-20260914-REPORT01"
    assert rows[0]["repository"].startswith("'=")
    assert rows[0]["artifact_result"] == "IMPORTED"
    assert rows[1]["name"] == "сервис"
    assert rows[1]["artifact_result"] == "CONFLICT"
    assert "[REDACTED]" in rows[1]["error_message"]


def test_pdf_report_uses_persisted_data_and_redacts_secrets(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = _create_completed_import(app, user_ids["operator"])

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        response = client.get(
            f"/api/operations/{operation_id}/report.pdf",
            headers=_auth(viewer),
        )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="operation-{operation_id}.pdf"'
    )
    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Harbor Transfer Portal" in text
    assert str(operation_id) in text
    assert "COMPLETED" in text
    assert "DELIVERY-20260914-REPORT01" in text
    assert "harbor-source.local" in text
    assert "artifact_conflict" in text
    assert SECRET_VALUE not in text
    if Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").is_file():
        assert "сервис" in text


def test_failed_operation_without_artifacts_still_has_report_row(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = _create_failed_export(app, user_ids["operator"])

    with TestClient(app) as client:
        viewer = _login(client, "viewer")
        response = client.get(
            f"/api/operations/{operation_id}/report.csv",
            headers=_auth(viewer),
        )

    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["operation_status"] == "FAILED"
    assert rows[0]["artifact_type"] == ""
    assert rows[0]["error_code"] == "export_failed"
    assert SECRET_VALUE not in response.text


def test_reports_require_auth_terminal_state_and_existing_operation(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = app.state.operation_manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=user_ids["operator"],
        actor_username="operator",
    )

    with TestClient(app) as client:
        assert client.get(f"/api/operations/{operation_id}/report.csv").status_code == 401
        viewer = _login(client, "viewer")
        not_ready = client.get(
            f"/api/operations/{operation_id}/report.csv",
            headers=_auth(viewer),
        )
        missing = client.get("/api/operations/999/report.pdf", headers=_auth(viewer))

    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "operation_report_not_ready"
    assert missing.status_code == 404


def test_canonical_receipt_download_preserves_owner_admin_policy(tmp_path: Path) -> None:
    app, user_ids = _app_with_users(tmp_path)
    operation_id = _create_completed_import(app, user_ids["operator"])

    with TestClient(app) as client:
        owner = _login(client, "operator")
        other = _login(client, "other")
        viewer = _login(client, "viewer")
        admin = _login(client, "admin")

        owner_response = client.get(
            f"/api/imports/{operation_id}/receipt/download",
            headers=_auth(owner),
        )
        other_response = client.get(
            f"/api/imports/{operation_id}/receipt/download",
            headers=_auth(other),
        )
        viewer_response = client.get(
            f"/api/imports/{operation_id}/receipt/download",
            headers=_auth(viewer),
        )
        admin_response = client.get(
            f"/api/imports/{operation_id}/receipt/download",
            headers=_auth(admin),
        )

    assert owner_response.status_code == 200
    assert owner_response.headers["content-disposition"] == (
        f'attachment; filename="import-receipt-{operation_id}.json"'
    )
    assert owner_response.headers["x-content-type-options"] == "nosniff"
    assert owner_response.json()["operation_id"] == operation_id
    assert owner_response.json()["source_delivery_id"] == "DELIVERY-20260914-REPORT01"
    assert admin_response.status_code == 200
    assert other_response.status_code == 403
    assert viewer_response.status_code == 403
