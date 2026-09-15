from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from re import fullmatch

from app.schemas.imports import (
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanRequest,
)

_REPOSITORY_COMPONENT_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"


def normalized_mapping(mapping: ImportDestinationPlanRequest) -> dict[str, object]:
    return {
        "mapping_policy_revision": mapping.mapping_policy_revision,
        "container_image_project": mapping.container_image_project,
        "helm_chart_project": mapping.helm_chart_project,
        "project_mappings": {
            key: mapping.project_mappings[key] for key in sorted(mapping.project_mappings)
        },
        "artifact_overrides": [
            item.model_dump(mode="json")
            for item in sorted(mapping.artifact_overrides, key=lambda item: item.index)
        ],
    }


def canonical_plan_hash(
    *,
    operation_id: int,
    source_delivery_id: str,
    actor_username: str,
    bundle_sha256: str,
    created_at: datetime,
    mapping: ImportDestinationPlanRequest,
    artifacts: list[ImportDestinationArtifactPlanResponse],
) -> str:
    created_at_utc = created_at.astimezone(UTC)
    identity = {
        "version": 2,
        "operation_id": operation_id,
        "source_delivery_id": source_delivery_id,
        "actor_username": actor_username,
        "bundle_sha256": bundle_sha256,
        "created_at": created_at_utc.isoformat(),
        "mapping": normalized_mapping(mapping),
        "artifacts": [
            {
                "index": item.index,
                "artifact_type": item.artifact_type,
                "source_repository": item.source_repository,
                "source_project": item.source_project,
                "name": item.name,
                "reference": item.reference,
                "version": item.version,
                "expected_digest": item.expected_digest,
                "payload_size": item.payload_size,
                "target_project": item.target_project,
                "target_repository": item.target_repository,
                "final_reference": item.final_reference,
                "project_exists": item.project_exists,
                "write_allowed": item.write_allowed,
                "target_digest": item.target_digest,
                "classification": item.classification.value,
                "error_code": item.error_code,
            }
            for item in sorted(artifacts, key=lambda item: item.index)
        ],
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _registry_coordinate(
    item: ImportDestinationArtifactPlanResponse,
) -> tuple[str, str] | None:
    """Return the actual OCI registry repository/reference mutated by an artifact."""
    if item.target_repository is None:
        return None
    if item.artifact_type == "container-image":
        if item.reference is None:
            return None
        return item.target_repository, item.reference
    if item.artifact_type == "helm-chart":
        if item.name is None or item.version is None:
            return None
        return f"{item.target_repository}/{item.name}", item.version
    return None


def colliding_artifact_indices(
    artifacts: list[ImportDestinationArtifactPlanResponse],
) -> set[int]:
    destinations: dict[tuple[str, str], list[int]] = {}
    for item in artifacts:
        coordinate = _registry_coordinate(item)
        if coordinate is None:
            continue
        destinations.setdefault(coordinate, []).append(item.index)
    return {
        index
        for indices in destinations.values()
        if len(indices) > 1
        for index in indices
    }


def validated_source_repository(repository: str) -> tuple[str, str]:
    if not repository or "\\" in repository or "://" in repository:
        raise ValueError("SOURCE repository имеет недопустимый формат")
    components = repository.split("/")
    if any(
        not component or fullmatch(_REPOSITORY_COMPONENT_PATTERN, component) is None
        for component in components
    ):
        raise ValueError("SOURCE repository содержит недопустимый path component")
    source_project = components[0]
    suffix = "/".join(components[1:])
    return source_project, suffix
