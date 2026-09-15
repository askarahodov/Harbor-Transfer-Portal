import pytest
from pydantic import ValidationError

from app.schemas.imports import (
    ImportArtifactDestinationOverride,
    ImportDestinationPlanRequest,
)


def test_destination_mapping_rejects_duplicate_artifact_overrides() -> None:
    with pytest.raises(ValidationError):
        ImportDestinationPlanRequest(
            container_image_project="docker",
            artifact_overrides=[
                ImportArtifactDestinationOverride(index=0, target_project="docker-a"),
                ImportArtifactDestinationOverride(index=0, target_project="docker-b"),
            ],
        )


def test_destination_mapping_rejects_non_normalized_project_names() -> None:
    with pytest.raises(ValidationError):
        ImportDestinationPlanRequest(project_mappings={"source": "../unsafe"})
