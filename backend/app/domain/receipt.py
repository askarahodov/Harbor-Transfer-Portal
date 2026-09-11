from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.bundle import ArtifactStatus, OperationStatus


class ArtifactReceipt(BaseModel):
    artifact_index: int = Field(ge=0)
    status: ArtifactStatus
    message: str | None = None
    target_digest: str | None = None


class ImportReceipt(BaseModel):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    delivery_id: str = Field(pattern=r"^DELIVERY-[0-9]{8}-[A-Z0-9]{6,32}$")
    operation_id: str
    status: Literal[
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.REJECTED,
        OperationStatus.CANCELLED,
    ]
    completed_at: datetime
    artifacts: list[ArtifactReceipt]

    @field_validator("completed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware UTC")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("completed_at must be UTC")
        return value
