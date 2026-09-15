from __future__ import annotations

import json
from typing import Any

from app.schemas.imports import ImportDestinationPlanResponse

AUDIT_DESTINATION_SAMPLE_LIMIT = 20


def destination_plan_audit_metadata(
    plan: ImportDestinationPlanResponse,
) -> dict[str, object]:
    destinations = [
        {
            "index": item.index,
            "artifact_type": item.artifact_type,
            "source_repository": item.source_repository,
            "target_repository": item.target_repository,
            "final_reference": item.final_reference,
        }
        for item in sorted(plan.artifacts, key=lambda item: item.index)
    ]
    return {
        "destination_plan_id": plan.plan_id,
        "destination_plan_hash": plan.plan_hash,
        "mapping_policy_revision": plan.mapping_policy_revision,
        "destination_count": len(destinations),
        "destinations": destinations[:AUDIT_DESTINATION_SAMPLE_LIMIT],
        "destinations_truncated": len(destinations) > AUDIT_DESTINATION_SAMPLE_LIMIT,
    }


def persisted_destination_plan_audit_metadata(
    import_policy_json: str | None,
) -> dict[str, object]:
    if not import_policy_json:
        return {}
    try:
        policy = json.loads(import_policy_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(policy, dict):
        return {}
    raw_plan: Any = policy.get("destination_plan")
    if raw_plan is None:
        return {}
    try:
        plan = ImportDestinationPlanResponse.model_validate(raw_plan)
    except ValueError:
        return {}
    return destination_plan_audit_metadata(plan)
