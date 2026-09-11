from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.bundle import OperationStatus
from app.schemas.harbor import ArtifactKind

_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"
_PROJECT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
_REPOSITORY_PATTERN = (
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)


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
        if any(character.isspace() for character in self.reference):
            raise ValueError("artifact reference must not contain whitespace")
        if "\x00" in self.reference or "/" in self.reference or "\\" in self.reference:
            raise ValueError("artifact reference contains unsupported characters")
        if self.kind is ArtifactKind.HELM_CHART and self.reference.startswith("sha256:"):
            raise ValueError("Helm export requires a version/tag reference")
        return self


class ExportSelectionRequest(BaseModel):
    artifacts: list[ExportArtifactSelection] = Field(min_length=1, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)

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
            (
                artifact.kind,
                artifact.project,
                artifact.repository,
                artifact.reference,
                artifact.digest,
            )
            for artifact in self.artifacts
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("export selection contains duplicate artifacts")
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
