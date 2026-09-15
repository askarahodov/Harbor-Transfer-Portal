from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.bundle import OperationStatus
from app.schemas.imports import ImportDestinationPlanResponse


class ImportRetryRequest(BaseModel):
    destination_plan_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class ImportRetryResponse(BaseModel):
    operation_id: int
    retry_of_operation_id: int
    status: OperationStatus
    failure_policy: Literal["continue-on-error"] = "continue-on-error"
    destination_plan: ImportDestinationPlanResponse


class ImportRetryLineage(BaseModel):
    retry_of_operation_id: int = Field(gt=0)
    source_plan_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    failure_policy: Literal["continue-on-error"]


def retry_lineage_from_policy(policy_json: str | None) -> ImportRetryLineage | None:
    if policy_json is None:
        return None
    try:
        payload = json.loads(policy_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    retry = payload.get("retry")
    if not isinstance(retry, dict):
        return None
    try:
        return ImportRetryLineage.model_validate(retry)
    except ValueError:
        return None
