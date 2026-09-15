from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportArtifactDestinationOverride,
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanRequest,
    destination_plan_id,
)
from app.services.destination_plan_integrity import (
    canonical_plan_hash,
    colliding_artifact_indices,
    validated_source_repository,
)

BUNDLE_SHA = "c" * 64
IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
CREATED_AT = datetime(2026, 9, 15, 8, 30, tzinfo=UTC)


def _image(
    *,
    index: int = 0,
    target_repository: str = "docker-prod/app/api",
    reference: str = "1.4.2",
) -> ImportDestinationArtifactPlanResponse:
    return ImportDestinationArtifactPlanResponse(
        index=index,
        artifact_type="container-image",
        source_repository="source-team/app/api",
        source_project="source-team",
        reference=reference,
        expected_digest=IMAGE_DIGEST,
        payload_size=1024,
        target_project="docker-prod",
        target_repository=target_repository,
        final_reference=f"harbor.target.local/{target_repository}:{reference}",
        project_exists=True,
        write_allowed=True,
        classification=ImportPreviewState.NEW,
    )


def _chart(
    *,
    index: int = 1,
    target_repository: str = "helm-prod/platform",
    name: str = "mis",
    version: str = "4.88.6",
) -> ImportDestinationArtifactPlanResponse:
    return ImportDestinationArtifactPlanResponse(
        index=index,
        artifact_type="helm-chart",
        source_repository="source-charts/platform",
        source_project="source-charts",
        name=name,
        version=version,
        expected_digest=CHART_DIGEST,
        payload_size=2048,
        target_project="helm-prod",
        target_repository=target_repository,
        final_reference=f"oci://harbor.target.local/{target_repository}/{name}:{version}",
        project_exists=True,
        write_allowed=True,
        classification=ImportPreviewState.NEW,
    )


def _hash(
    mapping: ImportDestinationPlanRequest,
    artifacts: list[ImportDestinationArtifactPlanResponse],
    *,
    actor_username: str = "operator",
) -> str:
    return canonical_plan_hash(
        operation_id=7,
        source_delivery_id="DELIVERY-20260915-PLAN01",
        actor_username=actor_username,
        bundle_sha256=BUNDLE_SHA,
        created_at=CREATED_AT,
        mapping=mapping,
        artifacts=artifacts,
    )


def test_canonical_plan_hash_is_stable_for_equivalent_ordering() -> None:
    first_mapping = ImportDestinationPlanRequest(
        container_image_project="docker-prod",
        helm_chart_project="helm-prod",
        project_mappings={"source-team": "docker-prod", "source-charts": "helm-prod"},
        artifact_overrides=[
            ImportArtifactDestinationOverride(index=1, target_project="helm-override"),
            ImportArtifactDestinationOverride(index=0, target_project="docker-override"),
        ],
    )
    second_mapping = ImportDestinationPlanRequest(
        container_image_project="docker-prod",
        helm_chart_project="helm-prod",
        project_mappings={"source-charts": "helm-prod", "source-team": "docker-prod"},
        artifact_overrides=[
            ImportArtifactDestinationOverride(index=0, target_project="docker-override"),
            ImportArtifactDestinationOverride(index=1, target_project="helm-override"),
        ],
    )

    assert _hash(first_mapping, [_image(), _chart()]) == _hash(
        second_mapping,
        [_chart(), _image()],
    )


def test_canonical_plan_hash_changes_for_security_relevant_content() -> None:
    mapping = ImportDestinationPlanRequest(
        container_image_project="docker-prod",
        helm_chart_project="helm-prod",
    )
    baseline = _hash(mapping, [_image(), _chart()])
    changed_target = _hash(
        mapping,
        [_image(target_repository="docker-other/app/api"), _chart()],
    )
    changed_actor = _hash(mapping, [_image(), _chart()], actor_username="admin")

    assert baseline != changed_target
    assert baseline != changed_actor


def test_policy_revision_changes_plan_hash_but_not_resolved_plan_id() -> None:
    artifacts = [_image(), _chart()]
    revision_one = ImportDestinationPlanRequest(
        mapping_policy_revision=1,
        container_image_project="docker-prod",
        helm_chart_project="helm-prod",
    )
    revision_two = revision_one.model_copy(update={"mapping_policy_revision": 2})

    assert _hash(revision_one, artifacts) != _hash(revision_two, artifacts)
    assert destination_plan_id(BUNDLE_SHA, artifacts) == destination_plan_id(BUNDLE_SHA, artifacts)


def test_collision_guard_blocks_same_kind_duplicate_registry_coordinate() -> None:
    first = _image(index=0, target_repository="docker-prod/app/api", reference="1.4.2")
    second = _image(index=1, target_repository="docker-prod/app/api", reference="1.4.2")

    assert colliding_artifact_indices([first, second]) == {0, 1}


def test_collision_guard_blocks_cross_kind_same_oci_repository_and_tag() -> None:
    image = _image(
        index=0,
        target_repository="shared/platform/mis",
        reference="4.88.6",
    )
    chart = _chart(
        index=1,
        target_repository="shared/platform",
        name="mis",
        version="4.88.6",
    )

    assert image.final_reference != chart.final_reference
    assert colliding_artifact_indices([image, chart]) == {0, 1}


@pytest.mark.parametrize(
    "repository",
    [
        "https://evil.example/project/app",
        "docker://evil.example/project/app",
        "../project/app",
        "project/../app",
        "project//app",
        r"project\\app",
        "project/app:tag",
    ],
)
def test_source_repository_validation_rejects_authority_and_traversal(repository: str) -> None:
    with pytest.raises(ValueError):
        validated_source_repository(repository)


def test_source_repository_validation_preserves_normalized_local_path() -> None:
    assert validated_source_repository("source-team/app/api") == (
        "source-team",
        "app/api",
    )
