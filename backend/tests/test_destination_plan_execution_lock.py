import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.imports import ImportDestinationPlanRequest
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
