from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.bundle import ArtifactStatus, OperationStatus
from app.domain.imports import ImportIntakeMode, ImportPreviewState


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


class ImportExecuteRequest(BaseModel):
    overwrite_conflicts: bool = False


class ImportStartResponse(BaseModel):
    operation_id: int
    status: OperationStatus


class ImportReceiptArtifactResponse(BaseModel):
    index: int
    artifact_type: str
    repository: str
    name: str | None = None
    reference: str | None = None
    version: str | None = None
    expected_digest: str | None = None
    target_digest: str | None = None
    status: ArtifactStatus
    error_code: str | None = None
    error_message: str | None = None


class ImportReceiptResponse(BaseModel):
    operation_id: int
    source_delivery_id: str
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor_username: str
    started_at: datetime
    finished_at: datetime
    overwrite_conflicts: bool
    result: str
    artifacts: list[ImportReceiptArtifactResponse]
