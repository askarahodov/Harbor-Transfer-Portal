from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.bundle import OperationStatus
from app.domain.imports import ImportIntakeMode, ImportPreviewState

_TARGET_PROJECT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"


class ImportIntakeResponse(BaseModel):
    operation_id: int
    status: OperationStatus
    intake_mode: ImportIntakeMode


class ImportDiscoveryResponse(BaseModel):
    operations: list[ImportIntakeResponse]


class ImportArtifactPreviewResponse(BaseModel):
    index: int
    artifact_type: str
    repository: str
    name: str | None = None
    reference: str | None = None
    version: str | None = None
    expected_digest: str | None = None
    target_digest: str | None = None
    payload_size: int = Field(ge=0)
    classification: ImportPreviewState
    error_code: str | None = None
    message: str | None = None


class ImportReceiptArtifactResponse(BaseModel):
    index: int
    artifact_type: str
    repository: str
    name: str | None = None
    reference: str | None = None
    version: str | None = None
    expected_digest: str | None = None
    target_digest: str | None = None
    source_project: str | None = None
    source_repository: str | None = None
    source_reference: str | None = None
    source_digest: str | None = None
    target_project: str | None = None
    target_repository: str | None = None
    target_reference: str | None = None
    artifact_kind: str | None = None
    result: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    size_bytes: int | None = None
    destination_plan_id: str | None = None
    overwrite_decision: str | None = None


class ImportPreviewResponse(BaseModel):
    operation_id: int
    status: OperationStatus
    source_delivery_id: str
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_size_bytes: int = Field(ge=1)
    signing_key_fingerprint: str
    verified_at: datetime

    # UI projection. Defaults keep already-persisted READY previews from older
    # v1 builds readable after an application upgrade.
    bundle_filename: str | None = None
    intake_mode: ImportIntakeMode | None = None
    source_harbor: str | None = None
    source_portal_version: str | None = None
    source_created_at: datetime | None = None
    source_created_by: str | None = None
    source_comment: str | None = None
    checksum_verified: bool = False
    signature_verified: bool = False
    schema_verified: bool = False
    overwrite_allowed: bool = False

    artifacts: list[ImportArtifactPreviewResponse]


class ImportArtifactDestinationOverride(BaseModel):
    index: int = Field(ge=0)
    target_project: str = Field(min_length=1, max_length=255, pattern=_TARGET_PROJECT_PATTERN)


class ImportDestinationPlanRequest(BaseModel):
    container_image_project: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=_TARGET_PROJECT_PATTERN,
    )
    helm_chart_project: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=_TARGET_PROJECT_PATTERN,
    )


class ImportDestinationArtifactPlanResponse(BaseModel):
    index: int = Field(ge=0)
    artifact_type: str
    source_repository: str
    source_project: str
    name: str | None = None
    reference: str | None = None
    version: str | None = None
    expected_digest: str | None = None
    payload_size: int = Field(ge=0)
    target_project: str | None = None
    target_repository: str | None = None
    final_reference: str | None = None
    project_exists: bool = False
    write_allowed: bool = False
    target_digest: str | None = None
    classification: ImportPreviewState
    error_code: str | None = None
    message: str | None = None


class ImportDestinationPlanResponse(BaseModel):
    operation_id: int
    status: OperationStatus
    destination_plan_id: str
    plan_hash: str
    artifacts: list[ImportDestinationArtifactPlanResponse]


class ImportExecuteRequest(BaseModel):
    overwrite_conflicts: bool = False


class ImportStartResponse(BaseModel):
    operation_id: int
    status: OperationStatus
    delivery_id: str | None = None


class ImportReceiptResponse(BaseModel):
    operation_id: int
    source_delivery_id: str
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor_username: str
    started_at: datetime
    finished_at: datetime
    overwrite_conflicts: bool
    destination_plan_id: str | None = None
    destination_plan_hash: str | None = None
    retry_of_operation_id: int | None = None
    failure_policy: str | None = None
    result: str
    artifacts: list[ImportReceiptArtifactResponse]


class ImportRetryRequest(BaseModel):
    pass


class ImportRetryResponse(BaseModel):
    operation_id: int
    retry_of_operation_id: int
    status: OperationStatus
    destination_plan_id: str | None = None
    artifact_count: int
    message: str
