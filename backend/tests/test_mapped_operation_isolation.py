import csv
import io
from pathlib import Path

from app.db.base import Base
from app.db.models import ArtifactResult, Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.services.report_service import iter_operation_csv


def _csv_rows(operation: Operation) -> list[dict[str, str]]:
    payload = b"".join(iter_operation_csv(operation)).decode()
    return list(csv.DictReader(io.StringIO(payload)))


def test_same_source_bundle_keeps_distinct_mapping_outcomes(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'mapping-isolation.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        first = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.COMPLETED,
            actor_username="operator",
            source_delivery_id="DELIVERY-SAME-SOURCE",
            bundle_sha256="a" * 64,
            total_artifacts=1,
            skipped_artifacts=1,
        )
        second = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.FAILED,
            actor_username="operator",
            source_delivery_id="DELIVERY-SAME-SOURCE",
            bundle_sha256="a" * 64,
            total_artifacts=1,
            failed_artifacts=1,
        )
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                ArtifactResult(
                    operation_id=first.id,
                    artifact_type="container-image",
                    repository="source-team/app",
                    reference="1.0.0",
                    source_digest="sha256:" + "b" * 64,
                    source_project="source-team",
                    source_repository="source-team/app",
                    source_reference="1.0.0",
                    target_project="target-a",
                    target_repository="target-a/app",
                    target_reference="harbor.target.local/target-a/app:1.0.0",
                    destination_plan_id="1" * 64,
                    destination_plan_hash="2" * 64,
                    overwrite_approved=False,
                    status=ArtifactStatus.SKIPPED,
                ),
                ArtifactResult(
                    operation_id=second.id,
                    artifact_type="container-image",
                    repository="source-team/app",
                    reference="1.0.0",
                    source_digest="sha256:" + "b" * 64,
                    source_project="source-team",
                    source_repository="source-team/app",
                    source_reference="1.0.0",
                    target_project="target-b",
                    target_repository="target-b/app",
                    target_reference="harbor.target.local/target-b/app:1.0.0",
                    destination_plan_id="3" * 64,
                    destination_plan_hash="4" * 64,
                    overwrite_approved=True,
                    status=ArtifactStatus.FAILED,
                    error_code="import_target_verification_failed",
                ),
            ]
        )
        session.commit()

        first_rows = _csv_rows(first)
        second_rows = _csv_rows(second)

    assert first_rows[0]["source_repository"] == "source-team/app"
    assert first_rows[0]["target_reference"] == "harbor.target.local/target-a/app:1.0.0"
    assert first_rows[0]["destination_plan_id"] == "1" * 64
    assert first_rows[0]["artifact_result"] == "SKIPPED"
    assert "target-b" not in str(first_rows)

    assert second_rows[0]["source_repository"] == "source-team/app"
    assert second_rows[0]["target_reference"] == "harbor.target.local/target-b/app:1.0.0"
    assert second_rows[0]["destination_plan_id"] == "3" * 64
    assert second_rows[0]["artifact_result"] == "FAILED"
    assert second_rows[0]["error_code"] == "import_target_verification_failed"
    assert "target-a" not in str(second_rows)
