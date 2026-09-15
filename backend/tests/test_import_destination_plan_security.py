import pytest

from app.domain.bundle import ContainerImageArtifact
from app.domain.imports import ImportPreviewState
from app.schemas.imports import ImportDestinationArtifactPlanResponse
from app.services.bundle_package_service import BundlePackageError
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator


def test_destination_plan_rejects_source_identity_mismatch() -> None:
    descriptor = ContainerImageArtifact(
        repository="source-team/app/api",
        reference="1.4.2",
        source_digest="sha256:" + "a" * 64,
        payload_path="images/api",
        payload_sha256="b" * 64,
        payload_size=123,
    )
    planned = ImportDestinationArtifactPlanResponse(
        index=0,
        artifact_type="container-image",
        source_repository="source-team/app/other",
        source_project="source-team",
        reference="1.4.2",
        expected_digest="sha256:" + "a" * 64,
        payload_size=123,
        target_project="docker-prod",
        target_repository="docker-prod/app/api",
        final_reference="harbor.target.local/docker-prod/app/api:1.4.2",
        project_exists=True,
        write_allowed=True,
        classification=ImportPreviewState.NEW,
    )

    with pytest.raises(BundlePackageError) as exc:
        ImportDestinationPlanOrchestrator._assert_plan_matches_descriptor(0, descriptor, planned)

    assert exc.value.code == "import_destination_plan_tampered"
