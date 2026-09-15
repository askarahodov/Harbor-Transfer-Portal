from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.artifacts import ArtifactKind

_TARGET_PROJECT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"


class HarborProjectResponse(BaseModel):
    name: str
    public: bool


class HarborProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255, pattern=_TARGET_PROJECT_PATTERN)
    public: bool = False
    operation_id: int | None = Field(default=None, ge=1)


class HarborProjectCreateResponse(BaseModel):
    name: str
    public: bool
    created: bool


class HarborRepositoryResponse(BaseModel):
    name: str
    artifact_count: int | None = None
    pull_count: int | None = None


class HarborArtifactResponse(BaseModel):
    kind: ArtifactKind
    project: str
    repository: str
    references: list[str] = Field(default_factory=list)
    digest: str
    size: int | None = None
    pushed_at: datetime | None = None
    media_type: str | None = None
    artifact_type: str | None = None


class PageResponse(BaseModel):
    page: int
    page_size: int
    total: int


class HarborProjectsPage(BaseModel):
    pagination: PageResponse
    items: list[HarborProjectResponse]


class HarborRepositoriesPage(BaseModel):
    pagination: PageResponse
    items: list[HarborRepositoryResponse]


class HarborArtifactsPage(BaseModel):
    pagination: PageResponse
    items: list[HarborArtifactResponse]


class HarborConnectionResponse(BaseModel):
    connected: bool
    version: str | None = None
    auth_mode: str | None = None
