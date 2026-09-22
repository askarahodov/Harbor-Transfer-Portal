from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.artifacts import ArtifactKind
from app.domain.bundle import OperationStatus

_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"
_PROJECT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
_REPOSITORY_PATTERN = (
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)
_IMAGE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_IMAGE_DIGEST_PATTERN = re.compile(_SHA256_PATTERN)
_HELM_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}$")


class ExportArtifactSelection(BaseModel):
    kind: ArtifactKind
    project: str = Field(min_length=1, max_length=256, pattern=_PROJECT_PATTERN)
    repository: str = Field(min_length=1, max_length=512, pattern=_REPOSITORY_PATTERN)
    reference: str = Field(min_length=1, max_length=256)
    digest: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_supported_kind_and_reference(self) -> ExportArtifactSelection:
        if self.kind is ArtifactKind.UNKNOWN_OCI:
            raise ValueError("unknown OCI artifacts cannot be exported")
        if self.kind is ArtifactKind.CONTAINER_IMAGE:
            if not (
                _IMAGE_TAG_PATTERN.fullmatch(self.reference)
                or _IMAGE_DIGEST_PATTERN.fullmatch(self.reference)
            ):
                raise ValueError("container image reference must be an OCI tag or sha256 digest")
            return self
        if _HELM_VERSION_PATTERN.fullmatch(self.reference) is None:
            raise ValueError("Helm export requires a valid version/tag reference")
        return self


class ExportSelectionRequest(BaseModel):
    artifacts: list[ExportArtifactSelection] = Field(min_length=1, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)
    harbor_profile_id: str = Field(default="default", min_length=1, max_length=64)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return None
        if "\x00" in normalized:
            raise ValueError("comment contains unsupported characters")
        return normalized

    @model_validator(mode="after")
    def reject_duplicates(self) -> ExportSelectionRequest:
        keys = [
            (artifact.project, artifact.repository, artifact.reference)
            for artifact in self.artifacts
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("export selection contains duplicate artifact references")
        return self


class ExportResolvedArtifactResponse(BaseModel):
    kind: ArtifactKind
    project: str
    repository: str
    reference: str
    source_digest: str
    size_bytes: int | None = None


class ExportPreviewResponse(BaseModel):
    artifacts: list[ExportResolvedArtifactResponse]
    estimated_payload_bytes: int


class ExportStartResponse(BaseModel):
    operation_id: int
    delivery_id: str
    status: OperationStatus


class ExportBundleResponse(BaseModel):
    operation_id: int
    delivery_id: str
    archive_name: str
    archive_size: int
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    download_url: str


class ExportDownloadTicketResponse(BaseModel):
    download_url: str
    expires_in_seconds: int = Field(ge=1, le=600)
