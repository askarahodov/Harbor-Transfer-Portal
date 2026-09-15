from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanResponse,
)
from app.services.import_mapping_audit import (
    AUDIT_DESTINATION_SAMPLE_LIMIT,
    destination_plan_audit_metadata,
)


def test_destination_audit_summary_is_bounded() -> None:
    artifact_count = AUDIT_DESTINATION_SAMPLE_LIMIT + 5
    artifacts = [
        ImportDestinationArtifactPlanResponse(
            index=index,
            artifact_type="container-image",
            source_repository=f"source/app-{index}",
            source_project="source",
            reference="1.0.0",
            expected_digest="sha256:" + f"{index:064x}"[-64:],
            payload_size=1,
            target_project="target",
            target_repository=f"target/app-{index}",
            final_reference=f"harbor.target.local/target/app-{index}:1.0.0",
            project_exists=True,
            write_allowed=True,
            classification=ImportPreviewState.NEW,
        )
        for index in range(artifact_count)
    ]
    plan = ImportDestinationPlanResponse(
        operation_id=77,
        source_delivery_id="DELIVERY-AUDIT-BOUNDED",
        actor_username="operator",
        bundle_sha256="a" * 64,
        plan_id="0" * 64,
        plan_hash="b" * 64,
        mapping_policy_revision=9,
        created_at="2026-09-15T10:10:00Z",
        valid=True,
        artifacts=artifacts,
    )

    metadata = destination_plan_audit_metadata(plan)

    assert metadata["destination_plan_id"] == plan.plan_id
    assert metadata["destination_plan_hash"] == "b" * 64
    assert metadata["mapping_policy_revision"] == 9
    assert metadata["destination_count"] == artifact_count
    assert metadata["destinations_truncated"] is True
    destinations = metadata["destinations"]
    assert isinstance(destinations, list)
    assert len(destinations) == AUDIT_DESTINATION_SAMPLE_LIMIT
    assert destinations[0]["source_repository"] == "source/app-0"
    assert destinations[-1]["source_repository"] == (
        f"source/app-{AUDIT_DESTINATION_SAMPLE_LIMIT - 1}"
    )
