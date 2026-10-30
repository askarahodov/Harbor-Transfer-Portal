from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.harbor_client import HarborClient
from app.services.nexus_service import NexusService, NexusPhase, TargetState


@dataclass(frozen=True, slots=True)
class ImageReference:
    repository: str
    reference: str


@dataclass(frozen=True, slots=True)
class ImageInspection:
    digest: str
    media_type: str | None = None
    architecture: str | None = None
    os: str | None = None


@dataclass(frozen=True, slots=True)
class ExportResult:
    source_digest: str
    payload_digest: str
    payload_path: Path


@dataclass(frozen=True, slots=True)
class ImportResult:
    expected_digest: str
    target_digest: str
    verified: bool


@dataclass(frozen=True, slots=True)
class TargetInspection:
    state: TargetState
    digest: str | None


class RegistryClient(Protocol):
    """Common interface for registry clients (Harbor, Nexus, etc.)"""

    async def inspect_image(self, image: ImageReference) -> ImageInspection: ...

    async def export_image(self, image: ImageReference, destination: Path) -> ExportResult: ...

    async def import_image(
        self,
        payload: Path,
        target: ImageReference,
        *,
        expected_digest: str,
    ) -> ImportResult: ...

    async def inspect_target(
        self,
        image: ImageReference,
        *,
        expected_digest: str | None = None,
    ) -> TargetInspection: ...


def create_registry_client(
    registry_type: Literal["harbor", "nexus"],
    session: Session,
    settings: Settings,
    *,
    profile_id: str | None = None,
    progress: Callable | None = None,
) -> RegistryClient:
    """Factory function to create registry client based on type."""
    if registry_type == "harbor":
        from app.services.harbor_client import HarborClient
        return _HarborClientWrapper(session, settings, profile_id)
    elif registry_type == "nexus":
        from app.services.nexus_service import NexusService
        return _NexusServiceWrapper(session, settings, profile_id)
    else:
        raise ValueError(f"Unknown registry type: {registry_type}")


class _HarborClientWrapper:
    """Adapter to make HarborClient conform to RegistryClient protocol."""

    def __init__(self, session: Session, settings: Settings, profile_id: str | None = None):
        self.client = HarborClient(session, settings, harbor_profile_id=profile_id)

    async def inspect_image(self, image) -> ImageInspection:
        result = await self.client.inspect_image(image)
        return ImageInspection(
            digest=result.digest,
            media_type=result.media_type,
            architecture=result.architecture,
            os=result.os,
        )

    async def export_image(self, image, destination) -> ExportResult:
        result = await self.client.export_image(image, destination)
        return ExportResult(
            source_digest=result.source_digest,
            payload_digest=result.payload_digest,
            payload_path=result.payload_path,
        )

    async def import_image(self, payload, target, *, expected_digest) -> ImportResult:
        result = await self.client.import_image(payload, target, expected_digest=expected_digest)
        return ImportResult(
            expected_digest=result.expected_digest,
            target_digest=result.target_digest,
            verified=result.verified,
        )

    async def inspect_target(self, image, *, expected_digest=None) -> TargetInspection:
        result = await self.client.inspect_target(image, expected_digest=expected_digest)
        return TargetInspection(state=result.state, digest=result.digest)


class _NexusServiceWrapper:
    """Adapter to make NexusService conform to RegistryClient protocol."""

    def __init__(self, session, settings, profile_id: str | None = None):
        from app.services.nexus_service import NexusService
        self.service = NexusService(session, None, nexus_profile_id=profile_id)

    async def inspect_image(self, image) -> ImageInspection:
        result = await self.service.inspect_image(image)
        return ImageInspection(
            digest=result.digest,
            media_type=result.media_type,
            architecture=result.architecture,
            os=result.os,
        )

    async def export_image(self, image, destination) -> ExportResult:
        result = await self.service.export_image(image, destination)
        return ExportResult(
            source_digest=result.source_digest,
            payload_digest=result.payload_digest,
            payload_path=result.payload_path,
        )

    async def import_image(self, payload, target, *, expected_digest) -> ImportResult:
        result = await self.service.import_image(payload, target, expected_digest=expected_digest)
        return ImportResult(
            expected_digest=result.expected_digest,
            target_digest=result.target_digest,
            verified=result.verified,
        )

    async def inspect_target(self, image, *, expected_digest=None) -> TargetInspection:
        result = await self.service.inspect_target(image, expected_digest=expected_digest)
        return TargetInspection(state=result.state, digest=result.digest)