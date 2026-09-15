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
    source_project: str | None
    source_repository: str | None
    source_reference: str | None
    source_version: str | None
    target_project: str | None
    target_repository: str | None
    target_reference: str | None
    target_version: str | None
    destination_plan_id: str | None
    destination_plan_hash: str | None
    overwrite_approved: bool | None
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


class OperationBundleResponse(BaseModel):
    filename: str
    size_bytes: int
    sha256: str


class OperationSummaryResponse(BaseModel):
    id: int
    delivery_id: str | None
    type: OperationType
    status: OperationStatus
    actor_username: str
    comment: str | None
    retry_of_operation_id: int | None = None
    failure_policy: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    total_artifacts: int
    successful_artifacts: int
    failed_artifacts: int
    skipped_artifacts: int
    conflict_artifacts: int
    bundle: OperationBundleResponse | None


class OperationListResponse(BaseModel):
    items: list[OperationSummaryResponse]
    total: int
    limit: int
    offset: int


class OperationResponse(BaseModel):
    id: int
    delivery_id: str | None
    type: OperationType
    status: OperationStatus
    actor_username: str
    comment: str | None
    retry_of_operation_id: int | None = None
    failure_policy: str | None = None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    bundle: OperationBundleResponse | None
    progress: OperationProgressResponse
    artifacts: list[OperationArtifactResponse]
