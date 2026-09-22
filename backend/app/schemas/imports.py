from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.bundle import ArtifactStatus, OperationStatus
from app.domain.imports import ImportIntakeMode, ImportPreviewState

_TARGET_PROJECT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"


class ImportIntakeResponse(BaseModel):
    operation_id: int
    status: OperationStatus
    intake_mode: ImportIntakeMode


class MediaHandoffVerificationResponse(BaseModel):
    delivery_id: str
    signing_key_fingerprint: str
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_size_bytes: int = Field(ge=1)
    created_at: str
    created_by: str
    verified: bool = True


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


class ImportArtifactDestinationOverride(BaseModel):
    index: int = Field(ge=0)
    target_project: str = Field(min_length=1, max_length=255, pattern=_TARGET_PROJECT_PATTERN)


class ImportDestinationPlanRequest(BaseModel):
    # Optional because intake already pins a profile for new clients. Legacy/unbound
    # operations default to "default" when first bound server-side.
    harbor_profile_id: str | None = Field(default=None, min_length=1, max_length=64)

    # Server-owned snapshot marker. Client values are overwritten by the policy-aware
    # orchestrator before planning; the field exists so persisted mapping_request is
    # cryptographically bound to the policy revision used for that plan.
    mapping_policy_revision: int = Field(default=0, ge=0)
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
    project_mappings: dict[str, str] = Field(default_factory=dict)
    artifact_overrides: list[ImportArtifactDestinationOverride] = Field(default_factory=list)

    @field_validator("project_mappings")
    @classmethod
    def validate_project_mappings(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        normalized: dict[str, str] = {}
        pattern = re.compile(_TARGET_PROJECT_PATTERN)
        for source, target in value.items():
            if not pattern.fullmatch(source) or not pattern.fullmatch(target):
                raise ValueError("project mappings must use normalized Harbor project names")
            normalized[source] = target
        return normalized

    @field_validator("artifact_overrides")
    @classmethod
    def reject_duplicate_override_indices(
        cls,
        value: list[ImportArtifactDestinationOverride],
    ) -> list[ImportArtifactDestinationOverride]:
        indices = [item.index for item in value]
        if len(indices) != len(set(indices)):
            raise ValueError("artifact override indices must be unique")
        return value


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


def destination_plan_id(
    bundle_sha256: str,
    artifacts: list[ImportDestinationArtifactPlanResponse],
) -> str:
    """Stable identity of bundle + resolved mapping, excluding observed TARGET state."""
    identity = {
        "bundle_sha256": bundle_sha256,
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
                "target_project": item.target_project,
                "target_repository": item.target_repository,
                "final_reference": item.final_reference,
            }
            for item in sorted(artifacts, key=lambda item: item.index)
        ],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ImportDestinationPlanResponse(BaseModel):
    operation_id: int
    source_delivery_id: str
    actor_username: str
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_policy_revision: int = Field(default=0, ge=0)
    created_at: datetime
    valid: bool
    artifacts: list[ImportDestinationArtifactPlanResponse]

    @model_validator(mode="after")
    def normalize_derived_fields(self) -> ImportDestinationPlanResponse:
        # plan_id is deliberately stable across TARGET state drift and policy revisions
        # when the resolved destinations remain identical. plan_hash binds the revision.
        self.plan_id = destination_plan_id(self.bundle_sha256, self.artifacts)
        self.valid = all(
            item.project_exists
            and item.write_allowed
            and item.classification
            in {ImportPreviewState.NEW, ImportPreviewState.SAME, ImportPreviewState.CONFLICT}
            for item in self.artifacts
        )
        return self


class ImportExecuteRequest(BaseModel):
    overwrite_conflicts: bool = False
    destination_plan_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


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
    target_repository: str | None = None
    final_reference: str | None = None
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
    destination_plan_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    destination_plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    retry_of_operation_id: int | None = Field(default=None, gt=0)
    failure_policy: str | None = None
    result: str
    artifacts: list[ImportReceiptArtifactResponse]
