import asyncio
import json
from pathlib import Path

from app.db.base import Base
from app.db.models import ArtifactResult, Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanResponse,
)
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator


def test_policy_worker_commits_mapping_snapshot_before_parent_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'worker-snapshot.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    bundle_sha = "a" * 64
    delivery_id = "DELIVERY-SNAPSHOT-WORKER"

    with session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
            bundle_sha256=bundle_sha,
            source_delivery_id=delivery_id,
            import_policy_json=json.dumps({"overwrite_conflicts": True}),
            total_artifacts=1,
        )
        session.add(operation)
        session.flush()
        session.add(
            ArtifactResult(
                operation_id=operation.id,
                artifact_type="helm-chart",
                repository="source-charts/platform",
                name="mis",
                version="4.88.6",
                source_digest="sha256:" + "b" * 64,
                status=ArtifactStatus.PENDING,
            )
        )
        session.commit()
        operation_id = operation.id

    artifact = ImportDestinationArtifactPlanResponse(
        index=0,
        artifact_type="helm-chart",
        source_repository="source-charts/platform",
        source_project="source-charts",
        name="mis",
        version="4.88.6",
        expected_digest="sha256:" + "b" * 64,
        payload_size=1024,
        target_project="helm-prod",
        target_repository="helm-prod/platform",
        final_reference="oci://harbor.target.local/helm-prod/platform/mis:4.88.6",
        project_exists=True,
        write_allowed=True,
        classification=ImportPreviewState.NEW,
    )
    plan = ImportDestinationPlanResponse(
        operation_id=operation_id,
        source_delivery_id=delivery_id,
        actor_username="operator",
        bundle_sha256=bundle_sha,
        plan_id="0" * 64,
        plan_hash="d" * 64,
        created_at="2026-09-15T10:00:00Z",
        valid=True,
        artifacts=[artifact],
    )

    orchestrator = object.__new__(PolicyAwareImportDestinationPlanOrchestrator)
    orchestrator.session_factory = session_factory
    orchestrator.destination_plan = lambda _operation_id: plan  # type: ignore[method-assign]
    parent_observations: list[str] = []

    async def parent_worker(_self, _context, received_operation_id: int) -> None:  # type: ignore[no-untyped-def]
        assert received_operation_id == operation_id
        with session_factory() as session:
            operation = session.get(Operation, operation_id)
            assert operation is not None
            row = operation.artifacts[0]
            assert row.source_repository == "source-charts/platform"
            assert row.source_version == "4.88.6"
            assert row.target_project == "helm-prod"
            assert row.target_repository == "helm-prod/platform"
            assert row.target_reference == (
                "oci://harbor.target.local/helm-prod/platform/mis:4.88.6"
            )
            assert row.target_version == "4.88.6"
            assert row.destination_plan_id == plan.plan_id
            assert row.destination_plan_hash == plan.plan_hash
            assert row.overwrite_approved is True
        parent_observations.append("snapshot-visible")

    monkeypatch.setattr(ImportDestinationPlanOrchestrator, "_import_worker", parent_worker)

    asyncio.run(orchestrator._import_worker(object(), operation_id))  # type: ignore[arg-type]

    assert parent_observations == ["snapshot-visible"]
