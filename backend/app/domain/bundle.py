from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class OperationType(StrEnum):
    EXPORT = "EXPORT"
    IMPORT = "IMPORT"


class OperationStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RUNNING = "RUNNING"
    PACKAGING = "PACKAGING"
    VERIFYING = "VERIFYING"
    UPLOADED = "UPLOADED"
    DISCOVERED = "DISCOVERED"
    READY = "READY"
    IMPORTING = "IMPORTING"
    VERIFYING_TARGET = "VERIFYING_TARGET"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ArtifactStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    IMPORTED = "IMPORTED"
    SKIPPED = "SKIPPED"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"


class BundleSource(BaseModel):
    contour: Literal["SOURCE"]
    harbor: str
    portal_version: str | None = None


class ArtifactBase(BaseModel):
    payload_path: str
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_size: int = Field(ge=0)

    @field_validator("payload_path")
    @classmethod
    def validate_relative_payload_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not value or ".." in path.parts or "." in path.parts:
            raise ValueError("payload_path must be a normalized relative path")
        if "\\" in value or "\x00" in value:
            raise ValueError("payload_path contains unsupported characters")
        return value


class ContainerImageArtifact(ArtifactBase):
    type: Literal["container-image"] = "container-image"
    repository: str
    reference: str
    source_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class HelmChartArtifact(ArtifactBase):
    type: Literal["helm-chart"] = "helm-chart"
    repository: str
    name: str
    version: str
    source_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")


ArtifactDescriptor = Annotated[
    ContainerImageArtifact | HelmChartArtifact,
    Field(discriminator="type"),
]


class BundleManifest(BaseModel):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    delivery_id: str = Field(pattern=r"^DELIVERY-[0-9]{8}-[A-Z0-9]{6,32}$")
    created_at: datetime
    created_by: str
    source: BundleSource
    comment: str | None = Field(default=None, max_length=2000)
    artifacts: list[ArtifactDescriptor] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        offset = value.utcoffset() if value.tzinfo is not None else None
        if offset is None:
            raise ValueError("created_at must be timezone-aware UTC")
        if offset.total_seconds() != 0:
            raise ValueError("created_at must be UTC")
        return value
