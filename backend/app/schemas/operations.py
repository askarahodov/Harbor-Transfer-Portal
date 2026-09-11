from datetime import datetime

from pydantic import BaseModel

from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType


class OperationArtifactResponse(BaseModel):
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
    total_artifacts: int
    completed_artifacts: int
    running_artifacts: int
    successful_artifacts: int
    failed_artifacts: int
    skipped_artifacts: int
    conflict_artifacts: int
    progress_current: int
    progress_total: int
    current_phase: OperationStatus
    running_artifact_ids: list[int]


class OperationResponse(BaseModel):
    id: int
    delivery_id: str | None
    type: OperationType
    status: OperationStatus
    actor_username: str
    comment: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    progress: OperationProgressResponse
    artifacts: list[OperationArtifactResponse]
