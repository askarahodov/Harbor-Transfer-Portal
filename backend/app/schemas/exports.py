from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"
_PROJECT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
_REPOSITORY_PATTERN = (
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)
_REFERENCE_PATTERN = (
    r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|sha256:[a-f0-9]{64})$"
)
_VERSION_PATTERN = r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}$"


class ContainerImageSelection(BaseModel):
    type: Literal["container-image"] = "container-image"
    project: str = Field(min_length=1, max_length=255, pattern=_PROJECT_PATTERN)
    repository: str = Field(min_length=1, max_length=512, pattern=_REPOSITORY_PATTERN)
    reference: str = Field(min_length=1, max_length=128, pattern=_REFERENCE_PATTERN)
    source_digest: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class HelmChartSelection(BaseModel):
    type: Literal["helm-chart"] = "helm-chart"
    project: str = Field(min_length=1, max_length=255, pattern=_PROJECT_PATTERN)
    repository: str = Field(min_length=1, max_length=512, pattern=_REPOSITORY_PATTERN)
    version: str = Field(min_length=1, max_length=128, pattern=_VERSION_PATTERN)
    source_digest: str | None = Field(default=None, pattern=_SHA256_PATTERN)


ExportSelectionItem = Annotated[
    ContainerImageSelection | HelmChartSelection,
    Field(discriminator="type"),
]


class ExportSelectionRequest(BaseModel):
    artifacts: list[ExportSelectionItem] = Field(min_length=1, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)


class ExportArtifactPreview(BaseModel):
    type: Literal["container-image", "helm-chart"]
    project: str
    repository: str
    reference: str | None = None
    name: str | None = None
    version: str | None = None
    source_digest: str
    size_bytes: int | None = Field(default=None, ge=0)


class ExportPreviewResponse(BaseModel):
    contour: Literal["SOURCE"] = "SOURCE"
    source_harbor: str
    artifacts: list[ExportArtifactPreview]
    known_size_bytes: int = Field(ge=0)
    unknown_size_count: int = Field(ge=0)


class ExportStartResponse(BaseModel):
    operation_id: int
    delivery_id: str
    status_url: str
    bundle_url: str


class ExportBundleMetadataResponse(BaseModel):
    operation_id: int
    delivery_id: str
    filename: str
    checksum_filename: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_count: int = Field(ge=1)
    download_url: str
    checksum_url: str
