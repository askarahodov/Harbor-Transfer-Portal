import asyncio

import pytest

from app.schemas.imports import (
    ImportArtifactDestinationOverride,
    ImportDestinationPlanRequest,
)
from app.services.import_orchestrator import ImportOrchestrationError

from test_import_destination_plan import _environment


def test_unknown_artifact_override_index_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    *_, orchestrator, operation_id = _environment(tmp_path)

    with pytest.raises(ImportOrchestrationError) as exc:
        asyncio.run(
            orchestrator.build_destination_plan(
                operation_id,
                ImportDestinationPlanRequest(
                    container_image_project="docker",
                    helm_chart_project="helm",
                    artifact_overrides=[
                        ImportArtifactDestinationOverride(index=99, target_project="other")
                    ],
                ),
            )
        )

    assert exc.value.code == "import_destination_override_invalid"
