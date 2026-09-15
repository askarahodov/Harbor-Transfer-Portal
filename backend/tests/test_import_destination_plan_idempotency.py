from app.domain.imports import ImportPreviewState
from app.schemas.imports import ImportDestinationArtifactPlanResponse
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator


def test_plan_id_depends_on_mapping_identity_not_dynamic_target_state() -> None:
    base = ImportDestinationArtifactPlanResponse(
        index=0,
        artifact_type="container-image",
        source_repository="source-team/app/api",
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
    changed_state = base.model_copy(
        update={
            "classification": ImportPreviewState.SAME,
            "target_digest": "sha256:" + "a" * 64,
        }
    )

    first = ImportDestinationPlanOrchestrator._plan_id("b" * 64, [base])
    second = ImportDestinationPlanOrchestrator._plan_id("b" * 64, [changed_state])

    assert first == second
