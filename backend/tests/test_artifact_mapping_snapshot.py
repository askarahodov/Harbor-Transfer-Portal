from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.base import Base
from app.db.models import ArtifactResult, Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanResponse,
)
from app.services.artifact_mapping_snapshot import persist_artifact_mapping_snapshot
from app.services.operation_manager import OperationTaskFailure


def _environment(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'snapshot.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    bundle_sha = "a" * 64
    delivery_id = "DELIVERY-SNAPSHOT-001"
    with session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
            bundle_sha256=bundle_sha,
            source_delivery_id=delivery_id,
            total_artifacts=1,
        )
        session.add(operation)
        session.flush()
        session.add(
            ArtifactResult(
                operation_id=operation.id,
                artifact_type="container-image",
                repository="source-team/apps/api",
                reference="1.4.2",
                source_digest="sha256:" + "b" * 64,
                status=ArtifactStatus.PENDING,
            )
        )
        session.commit()
        operation_id = operation.id
    return session_factory, operation_id, bundle_sha, delivery_id


def _plan(
    operation_id: int,
    bundle_sha: str,
    delivery_id: str,
    *,
    source_repository: str = "source-team/apps/api",
    write_allowed: bool = True,
) -> ImportDestinationPlanResponse:
    artifact = ImportDestinationArtifactPlanResponse(
        index=0,
        artifact_type="container-image",
        source_repository=source_repository,
        source_project="source-team",
        reference="1.4.2",
        expected_digest="sha256:" + "b" * 64,
        payload_size=1024,
        target_project="docker-prod",
        target_repository="docker-prod/apps/api",
        final_reference="harbor-target.local/docker-prod/apps/api:1.4.2",
        project_exists=True,
        write_allowed=write_allowed,
        classification=ImportPreviewState.NEW,
    )
    return ImportDestinationPlanResponse(
        operation_id=operation_id,
        source_delivery_id=delivery_id,
        actor_username="operator",
        bundle_sha256=bundle_sha,
        plan_id="0" * 64,
        plan_hash="d" * 64,
        created_at=datetime.now(UTC),
        valid=True,
        artifacts=[artifact],
    )


def test_snapshot_persists_exact_plan_identity_before_execution(tmp_path: Path) -> None:
    session_factory, operation_id, bundle_sha, delivery_id = _environment(tmp_path)
    plan = _plan(operation_id, bundle_sha, delivery_id)

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        persist_artifact_mapping_snapshot(
            session,
            operation,
            plan,
            overwrite_approved=True,
        )
        session.commit()

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        row = operation.artifacts[0]
        assert row.source_project == "source-team"
        assert row.source_repository == "source-team/apps/api"
        assert row.source_reference == "1.4.2"
        assert row.source_version is None
        assert row.target_project == "docker-prod"
        assert row.target_repository == "docker-prod/apps/api"
        assert row.target_reference == "harbor-target.local/docker-prod/apps/api:1.4.2"
        assert row.destination_plan_id == plan.plan_id
        assert row.destination_plan_hash == plan.plan_hash
        assert row.overwrite_approved is True


def test_snapshot_rejects_source_mismatch_without_partial_row_mutation(tmp_path: Path) -> None:
    session_factory, operation_id, bundle_sha, delivery_id = _environment(tmp_path)
    plan = _plan(
        operation_id,
        bundle_sha,
        delivery_id,
        source_repository="source-team/apps/other",
    )

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        row = operation.artifacts[0]

        with pytest.raises(OperationTaskFailure) as exc_info:
            persist_artifact_mapping_snapshot(
                session,
                operation,
                plan,
                overwrite_approved=False,
            )

        assert exc_info.value.code == "import_destination_snapshot_invalid"
        assert row.source_project is None
        assert row.target_project is None
        assert row.target_reference is None
        assert row.destination_plan_id is None
        assert row.overwrite_approved is None


def test_snapshot_rejects_invalid_plan_before_row_mutation(tmp_path: Path) -> None:
    session_factory, operation_id, bundle_sha, delivery_id = _environment(tmp_path)
    plan = _plan(
        operation_id,
        bundle_sha,
        delivery_id,
        write_allowed=False,
    )
    assert plan.valid is False

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        row = operation.artifacts[0]

        with pytest.raises(OperationTaskFailure) as exc_info:
            persist_artifact_mapping_snapshot(
                session,
                operation,
                plan,
                overwrite_approved=True,
            )

        assert exc_info.value.code == "import_destination_snapshot_invalid"
        assert row.source_repository is None
        assert row.target_repository is None
        assert row.destination_plan_hash is None
        assert row.overwrite_approved is None
