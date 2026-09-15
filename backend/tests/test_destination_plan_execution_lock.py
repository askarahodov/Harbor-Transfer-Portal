import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.schemas.imports import ImportDestinationPlanRequest, ImportDestinationPlanResponse
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator


def test_destination_plan_cannot_change_after_worker_claim() -> None:
    class _OperationManager:
        @staticmethod
        def get_operation(operation_id: int):  # type: ignore[no-untyped-def]
            assert operation_id == 17
            return SimpleNamespace(worker_token="claimed-worker-token")

    orchestrator = object.__new__(PolicyAwareImportDestinationPlanOrchestrator)
    orchestrator.operation_manager = _OperationManager()

    with pytest.raises(ImportOrchestrationError) as exc_info:
        asyncio.run(
            orchestrator.build_destination_plan(
                17,
                ImportDestinationPlanRequest(container_image_project="docker-prod"),
                actor_username="operator",
            )
        )

    assert exc_info.value.code == "import_destination_plan_stale"


def test_destination_plan_persistence_rejects_claimed_worker_atomically(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'plan-lock.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    bundle_sha = "a" * 64
    delivery_id = "DELIVERY-PLAN-LOCK"

    with session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
            bundle_sha256=bundle_sha,
            source_delivery_id=delivery_id,
            worker_token="claimed-worker-token",
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    plan = ImportDestinationPlanResponse(
        operation_id=operation_id,
        source_delivery_id=delivery_id,
        actor_username="operator",
        bundle_sha256=bundle_sha,
        plan_id="0" * 64,
        plan_hash="b" * 64,
        mapping_policy_revision=4,
        created_at="2026-09-15T10:15:00Z",
        valid=True,
        artifacts=[],
    )
    mapping = ImportDestinationPlanRequest(
        mapping_policy_revision=4,
        container_image_project="docker-prod",
    )
    orchestrator = object.__new__(PolicyAwareImportDestinationPlanOrchestrator)
    orchestrator.session_factory = session_factory

    with pytest.raises(ImportOrchestrationError) as exc_info:
        orchestrator._persist_destination_plan(operation_id, mapping, plan)

    assert exc_info.value.code == "import_destination_plan_stale"
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.import_policy_json is None
