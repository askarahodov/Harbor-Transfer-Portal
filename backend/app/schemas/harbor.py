from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.artifacts import ArtifactKind


class HarborProjectResponse(BaseModel):
    name: str
    public: bool


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
