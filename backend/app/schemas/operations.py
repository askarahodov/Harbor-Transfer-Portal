from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType


class OperationArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artifact_type: str
    repository: str
    name: str | None
    reference: str | None
    version: str | None
    source_digest: str | None
    target_digest: str | None
    status: ArtifactStatus
    error_code: str | None
    error_message: str | None
    size_bytes: int | None
    started_at: datetime | None
    finished_at: datetime | None


class OperationProgressResponse(BaseModel):
    current: int
    total: int
    completed_artifacts: int
    running_artifacts: int
    successful_artifacts: int
    failed_artifacts: int
    skipped_artifacts: int
    conflict_artifacts: int
    current_artifact_id: int | None


class OperationDetailResponse(BaseModel):
    id: int
    delivery_id: str | None
    type: OperationType
    status: OperationStatus
    actor_user_id: int | None
    actor_username: str
    comment: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    progress: OperationProgressResponse
    artifacts: list[OperationArtifactResponse]
