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
