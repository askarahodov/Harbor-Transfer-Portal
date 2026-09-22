from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.domain.artifacts import ArtifactKind
from app.domain.bundle import ArtifactStatus, OperationStatus
from app.schemas.exports import ExportArtifactSelection
from app.services.bundle_package_service import BundlePackageError, BundlePackageService
from app.services.export_orchestrator import ExportOrchestrator
from app.services.harbor_client import HarborArtifact
from app.services.helm_oci_service import (
    HelmPackageMetadata,
    HelmPullResult,
    HelmServiceError,
)
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import ExportResult

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
CHANGED_DIGEST = "sha256:" + "c" * 64


class FakeHarborClient:
    def __init__(self, artifacts: dict[tuple[str, str, str], HarborArtifact]) -> None:
        self.artifacts = artifacts
        self.calls: dict[tuple[str, str, str], int] = {}

    def get_artifact(self, project: str, repository: str, reference: str) -> HarborArtifact:
        key = (project, repository, reference)
        self.calls[key] = self.calls.get(key, 0) + 1
        return self.artifacts[key]

    def close(self) -> None:
        pass


class FakeSkopeoService:
    def __init__(self, digest: str = IMAGE_DIGEST) -> None:
        self.digest = digest

    async def export_image(self, _image, destination: Path) -> ExportResult:  # type: ignore[no-untyped-def]
        blobs = destination / "blobs" / "sha256"
        blobs.mkdir(parents=True)
        (destination / "oci-layout").write_text(
            '{"imageLayoutVersion":"1.0.0"}',
            encoding="utf-8",
        )
        (destination / "index.json").write_text('{"schemaVersion":2}', encoding="utf-8")
        (blobs / "blob").write_bytes(b"image-payload")
        return ExportResult(
            source_digest=self.digest,
            payload_digest=self.digest,
            payload_path=destination,
        )


class FakeHelmService:
    def __init__(self, digest: str = CHART_DIGEST, *, fail: bool = False) -> None:
        self.digest = digest
        self.fail = fail

    async def pull_chart(self, chart, destination: Path) -> HelmPullResult:  # type: ignore[no-untyped-def]
        if self.fail:
            raise HelmServiceError("helm_command_failed", "Helm export failed safely")
        destination.mkdir(parents=True)
        package = destination / f"{chart.name}-{chart.version}.tgz"
        package.write_bytes(b"chart-package")
        return HelmPullResult(
            source_digest=self.digest,
            package=HelmPackageMetadata(
                path=package,
                name=chart.name,
                version=chart.version,
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            ),
        )


class PublishThenFailPackageService:
    def __init__(self, delegate: BundlePackageService) -> None:
        self.delegate = delegate

    def allocate_delivery_id(self) -> str:
        return self.delegate.allocate_delivery_id()

    def build_bundle(self, **kwargs):  # type: ignore[no-untyped-def]
        self.delegate.build_bundle(**kwargs)
        raise BundlePackageError(
            "bundle_post_publish_failure",
            "Synthetic failure after publication",
        )


def _write_keys(tmp_path: Path) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "data" / "keys" / "source-private.pem"
    trusted_dir = tmp_path / "data" / "keys" / "trusted"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    trusted_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_path, 0o600)
    (trusted_dir / "source.pem").write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, trusted_dir


def _environment(tmp_path: Path):  # type: ignore[no-untyped-def]
    private_key, trusted_dir = _write_keys(tmp_path)
    data = tmp_path / "data"
    database_url = f"sqlite:///{tmp_path / 'export.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        harbor_url="https://harbor.source.local",
        database_url=database_url,
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        skopeo_payload_root=data,
        skopeo_temp_root=data / "tmp" / "skopeo",
        helm_workspace_root=data / "packages",
        helm_temp_root=data / "tmp" / "helm",
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "incoming" / "verified",
        bundle_signing_private_key_file=private_key,
        bundle_trusted_public_keys_dir=trusted_dir,
    )
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    harbor = FakeHarborClient(
        {
            ("team", "app", "1.0.0"): HarborArtifact(
                digest=IMAGE_DIGEST,
                type="IMAGE",
                size=101,
            ),
            ("team", "charts/sample", "1.2.3"): HarborArtifact(
                digest=CHART_DIGEST,
                type="CHART",
                size=202,
            ),
        }
    )
    package_service = BundlePackageService(settings)
    orchestrator = ExportOrchestrator(
        session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        skopeo_factory=lambda _session: FakeSkopeoService(),
        helm_factory=lambda _session: FakeHelmService(),
        package_factory=lambda: package_service,
    )
    return settings, manager, harbor, package_service, orchestrator


def _selections() -> tuple[ExportArtifactSelection, ExportArtifactSelection]:
    return (
        ExportArtifactSelection(
            kind=ArtifactKind.CONTAINER_IMAGE,
            project="team",
            repository="app",
            reference="1.0.0",
            digest=IMAGE_DIGEST,
        ),
        ExportArtifactSelection(
            kind=ArtifactKind.HELM_CHART,
            project="team",
            repository="charts/sample",
            reference="1.2.3",
            digest=CHART_DIGEST,
        ),
    )


def _outgoing_is_empty(settings: Settings) -> bool:
    root = settings.bundle_outgoing_root
    return not root.exists() or not any(root.iterdir())


def test_mixed_export_creates_one_signed_verified_bundle(tmp_path: Path) -> None:
    settings, manager, harbor, package_service, orchestrator = _environment(tmp_path)

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.start_export(
            _selections(),
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="operator",
            comment="release 1",
        )
        await manager.wait(started.operation_id)
        await manager.shutdown()
        return started.operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.COMPLETED
    assert operation.delivery_id is not None
    assert operation.harbor_profile_id == "default"
    assert operation.harbor_profile_name == "Default"
    assert operation.harbor_url == "https://harbor.source.local"
    assert [artifact.status for artifact in operation.artifacts] == [
        ArtifactStatus.VERIFIED,
        ArtifactStatus.VERIFIED,
    ]
    assert operation.successful_artifacts == 2
    assert harbor.calls == {
        ("team", "app", "1.0.0"): 1,
        ("team", "charts/sample", "1.2.3"): 1,
    }

    metadata = orchestrator.bundle_metadata(operation_id)
    handoff = orchestrator.handoff_metadata(operation_id)
    assert metadata.archive_size == metadata.archive_path.stat().st_size
    assert handoff.handoff_path.is_file()
    assert handoff.delivery_id == operation.delivery_id
    assert operation.handoff_filename == handoff.handoff_path.name
    assert operation.handoff_sha256 == handoff.sha256
    assert operation.handoff_size_bytes == handoff.handoff_size
    assert operation.bundle_signing_key_fingerprint == handoff.signing_key_fingerprint
    assert metadata.archive_path.parent == settings.bundle_outgoing_root.resolve()
    sidecar = metadata.archive_path.with_name(metadata.archive_path.name + ".sha256")
    verified = package_service.verify_bundle(metadata.archive_path, sidecar_path=sidecar)
    assert verified.manifest.delivery_id == operation.delivery_id
    assert len(verified.manifest.artifacts) == 2


def test_source_digest_change_after_start_validation_fails_without_second_harbor_lookup(
    tmp_path: Path,
) -> None:
    settings, manager, harbor, package_service, _orchestrator = _environment(tmp_path)
    orchestrator = ExportOrchestrator(
        manager.session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        skopeo_factory=lambda _session: FakeSkopeoService(digest=CHANGED_DIGEST),
        helm_factory=lambda _session: FakeHelmService(),
        package_factory=lambda: package_service,
    )

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.start_export(
            (_selections()[0],),
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="operator",
            comment=None,
        )
        await manager.wait(started.operation_id)
        await manager.shutdown()
        return started.operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "export_source_changed"
    assert operation.artifacts[0].status is ArtifactStatus.FAILED
    assert harbor.calls[("team", "app", "1.0.0")] == 1
    assert _outgoing_is_empty(settings)


def test_one_artifact_failure_aborts_delivery_and_marks_no_ready_bundle(tmp_path: Path) -> None:
    settings, manager, harbor, package_service, _orchestrator = _environment(tmp_path)
    orchestrator = ExportOrchestrator(
        manager.session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        skopeo_factory=lambda _session: FakeSkopeoService(),
        helm_factory=lambda _session: FakeHelmService(fail=True),
        package_factory=lambda: package_service,
    )

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.start_export(
            _selections(),
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="operator",
            comment=None,
        )
        await manager.wait(started.operation_id)
        await manager.shutdown()
        return started.operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "helm_command_failed"
    assert all(artifact.status is ArtifactStatus.FAILED for artifact in operation.artifacts)
    assert _outgoing_is_empty(settings)


def test_failure_after_publication_removes_archive_and_readiness_sidecar(tmp_path: Path) -> None:
    settings, manager, harbor, package_service, _orchestrator = _environment(tmp_path)
    orchestrator = ExportOrchestrator(
        manager.session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        skopeo_factory=lambda _session: FakeSkopeoService(),
        helm_factory=lambda _session: FakeHelmService(),
        package_factory=lambda: PublishThenFailPackageService(package_service),
    )

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.start_export(
            (_selections()[0],),
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="operator",
            comment=None,
        )
        await manager.wait(started.operation_id)
        await manager.shutdown()
        return started.operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "bundle_post_publish_failure"
    assert _outgoing_is_empty(settings)
