from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # Legacy Harbor 1.0 manifests keep their exact canonical representation.
    harbor: str | None = None
    portal_version: str | None = None
    registry_type: Literal["harbor", "nexus"] | None = None
    registry: str | None = None

    @model_validator(mode="after")
    def validate_registry_provenance(self) -> BundleSource:
        if self.registry_type == "nexus":
            if self.harbor is not None or not self.registry:
                raise ValueError("Nexus source requires registry and forbids source.harbor")
            # Source provenance must be an origin, never an auth-bearing URL,
            # transfer endpoint, or a path containing secrets. HTTP remains
            # accepted for explicitly isolated test connectors.
            try:
                parsed = urlsplit(self.registry)
                host, port = parsed.hostname, parsed.port
            except ValueError as exc:
                raise ValueError("Nexus registry origin is invalid") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not host or port == 0
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment
            ):
                raise ValueError("Nexus registry origin must not contain credentials or paths")
        elif not self.harbor:
            raise ValueError("Legacy Harbor source requires source.harbor")
        if self.registry_type == "harbor" and self.registry not in (None, self.harbor):
            raise ValueError("Harbor registry origin conflicts with source.harbor")
        return self


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

    @model_validator(mode="after")
    def validate_versioned_source(self) -> BundleManifest:
        # v1.0 has already been deployed to TARGET with Harbor-only meaning.
        # v1.1 introduces Nexus provenance; do not silently relabel either.
        if self.schema_version == "1.0":
            if self.source.registry_type is not None or self.source.registry is not None:
                raise ValueError("Bundle 1.0 cannot include registry provenance extensions")
        elif self.schema_version == "1.1" and self.source.registry_type != "nexus":
            raise ValueError("Bundle 1.1 requires explicit Nexus provenance")
        return self

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        offset = value.utcoffset() if value.tzinfo is not None else None
        if offset is None:
            raise ValueError("created_at must be timezone-aware UTC")
        if offset.total_seconds() != 0:
            raise ValueError("created_at must be UTC")
        return value
