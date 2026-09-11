import asyncio
import hashlib
from pathlib import Path

import pytest

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation, User, UserRole
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus
from app.schemas.exports import ExportSelectionRequest
from app.services.bundle_package_service import (
    BundleBuildResult,
    BundlePackageError,
    ContainerImagePackageInput,
    HelmChartPackageInput,
)
from app.services.export_orchestration import (
    ExportOrchestrationError,
    ExportOrchestrationService,
)
from app.services.harbor_client import HarborArtifact, HarborTag
from app.services.helm_oci_service import HelmPackageMetadata, HelmPullResult
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import ExportResult, SkopeoServiceError

_IMAGE_DIGEST = "sha256:" + "1" * 64
_CHART_DIGEST = "sha256:" + "2" * 64
_CHANGED_DIGEST = "sha256:" + "3" * 64
_DELIVERY_ID = "DELIVERY-20260911-AGENT2TEST01"


class FakeHarborClient:
    def __init__(
        self,
        artifacts: dict[tuple[str, str, str], HarborArtifact],
        *,
        base_url: str = "https://source-harbor.example",
    ) -> None:
        self.artifacts = artifacts
        self.base_url = base_url
        self.closed = False

    def get_artifact(self, project: str, repository: str, reference: str) -> HarborArtifact:
        return self.artifacts[(project, repository, reference)]

    def close(self) -> None:
        self.closed = True


class FakeSkopeo:
    def __init__(self, *, fail: bool = False, returned_digest: str | None = None) -> None:
        self.fail = fail
        self.returned_digest = returned_digest
        self.calls: list[tuple[str, str, Path]] = []

    async def export_image(self, image, destination: Path) -> ExportResult:
        self.calls.append((image.repository, image.reference, destination))
        if self.fail:
            raise SkopeoServiceError("skopeo_command_failed", "Skopeo export failed")
        destination.mkdir(parents=True)
        (destination / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (destination / "index.json").write_text("{}")
        (destination / "blobs").mkdir()
        digest = self.returned_digest or _IMAGE_DIGEST
        return ExportResult(
            source_digest=digest,
            payload_digest=digest,
            payload_path=destination,
        )


class FakeHelm:
    def __init__(self, *, returned_digest: str | None = None) -> None:
        self.returned_digest = returned_digest
        self.calls: list[tuple[str, str, str, Path]] = []

    async def pull_chart(self, chart, destination: Path) -> HelmPullResult:
        self.calls.append((chart.repository, chart.name, chart.version, destination))
        destination.mkdir(parents=True)
        package = destination / f"{chart.name}-{chart.version}.tgz"
        package.write_bytes(b"chart")
        digest = self.returned_digest or _CHART_DIGEST
        return HelmPullResult(
            source_digest=digest,
            package=HelmPackageMetadata(
                path=package,
                name=chart.name,
                version=chart.version,
                sha256=hashlib.sha256(b"chart").hexdigest(),
            ),
        )


class FakeBundle:
    def __init__(
        self,
        settings: Settings,
        *,
        fail: bool = False,
        delivery_id: str = _DELIVERY_ID,
    ) -> None:
        self.settings = settings
        self.fail = fail
        self.delivery_id = delivery_id
        self.artifacts = []

    def allocate_delivery_id(self) -> str:
        return self.delivery_id

    def build_bundle(
        self,
        *,
        source,
        created_by,
        artifacts,
        delivery_id,
        comment,
    ):
        if self.fail:
            raise BundlePackageError(
                "bundle_signature_untrusted",
                "Bundle self-verification failed",
            )
        self.artifacts.extend(artifacts)
        outgoing = self.settings.bundle_outgoing_root
        outgoing.mkdir(parents=True, exist_ok=True)
        archive = outgoing / f"{delivery_id}.htp.tar.gz"
        sidecar = outgoing / f"{archive.name}.sha256"
        archive.write_bytes(b"signed-bundle-payload")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar.write_text(f"{sha}  {archive.name}\n")
        from app.domain.bundle import BundleManifest

        manifest = BundleManifest(
            schema_version="1.0",
            delivery_id=delivery_id,
            created_at="2026-09-11T10:00:00Z",
            created_by=created_by,
            source=source,
            comment=comment,
            artifacts=[
                {
                    "type": "container-image",
                    "repository": "project/app",
                    "reference": "1.0",
                    "source_digest": _IMAGE_DIGEST,
                    "payload_path": "images/artifact-0001",
                    "payload_sha256": "a" * 64,
                    "payload_size": 1,
                }
            ],
        )
        return BundleBuildResult(
            manifest=manifest,
            archive_path=archive,
            sidecar_path=sidecar,
            archive_sha256=sha,
            archive_size=archive.stat().st_size,
            signing_key_fingerprint="sha256:" + "f" * 64,
        )


def _settings(tmp_path: Path, *, contour: PortalContour = PortalContour.SOURCE) -> Settings:
    return Settings(
        _env_file=None,
        portal_contour=contour,
        database_url=f"sqlite:///{tmp_path / 'operations.db'}",
        harbor_url="https://source-harbor.example",
        operation_workspace_root=tmp_path / "operation-work",
        operation_disk_reserve_bytes=0,
        bundle_payload_root=tmp_path,
        bundle_temp_root=tmp_path / "bundle-temp",
        bundle_outgoing_root=tmp_path / "outgoing",
        helm_workspace_root=tmp_path,
        skopeo_payload_root=tmp_path,
    )


def _environment(tmp_path: Path, settings: Settings):
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    manager = OperationManager(factory, settings)
    with factory() as session:
        user = User(
            username="operator",
            password_hash="not-used",
            role=UserRole.OPERATOR,
            is_active=True,
        )
        admin = User(
            username="admin",
            password_hash="not-used",
            role=UserRole.ADMIN,
            is_active=True,
        )
        other = User(
            username="other",
            password_hash="not-used",
            role=UserRole.OPERATOR,
            is_active=True,
        )
        session.add_all([user, admin, other])
        session.commit()
        return factory, manager, user.id, admin.id, other.id


def _actor(factory, user_id: int) -> User:
    with factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user


def _operation(factory, operation_id: int) -> Operation:
    with factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        _ = list(operation.artifacts)
        session.expunge_all()
        return operation


def _artifacts() -> dict[tuple[str, str, str], HarborArtifact]:
    return {
        ("project", "app", "1.0"): HarborArtifact(
            digest=_IMAGE_DIGEST,
            type="IMAGE",
            media_type="application/vnd.oci.image.manifest.v1+json",
            size=1024,
            tags=[HarborTag(name="1.0")],
        ),
        ("project", "charts/chart", "2.0.0"): HarborArtifact(
            digest=_CHART_DIGEST,
            type="CHART",
            media_type="application/vnd.cncf.helm.chart.content.v1.tar+gzip",
            size=2048,
            tags=[HarborTag(name="2.0.0")],
        ),
    }


def _request() -> ExportSelectionRequest:
    return ExportSelectionRequest.model_validate(
        {
            "comment": "release candidate",
            "artifacts": [
                {
                    "type": "container-image",
                    "project": "project",
                    "repository": "app",
                    "reference": "1.0",
                    "source_digest": _IMAGE_DIGEST,
                },
                {
                    "type": "helm-chart",
                    "project": "project",
                    "repository": "charts/chart",
                    "version": "2.0.0",
                    "source_digest": _CHART_DIGEST,
                },
            ],
        }
    )


def test_mixed_export_completes_and_publishes_controlled_bundle(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        factory, manager, user_id, admin_id, other_id = _environment(tmp_path, settings)
        artifacts = _artifacts()
        skopeo = FakeSkopeo()
        helm = FakeHelm()
        bundles: list[FakeBundle] = []

        def bundle_factory(service_settings: Settings) -> FakeBundle:
            bundle = FakeBundle(service_settings)
            bundles.append(bundle)
            return bundle

        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: FakeHarborClient(artifacts),
            skopeo_factory=lambda _session, _settings: skopeo,
            helm_factory=lambda _session, _settings: helm,
            bundle_factory=bundle_factory,
        )
        await manager.startup()
        actor = _actor(factory, user_id)
        handle, delivery_id = await service.start(_request(), actor)
        await manager.wait(handle.operation_id)

        operation = _operation(factory, handle.operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.COMPLETED
        assert operation.delivery_id == _DELIVERY_ID == delivery_id
        assert operation.progress_current == operation.progress_total == 2
        assert [item.status for item in operation.artifacts] == [
            ArtifactStatus.VERIFIED,
            ArtifactStatus.VERIFIED,
        ]

        bundle = service.published_bundle(handle.operation_id, actor)
        assert bundle.archive_path.read_bytes() == b"signed-bundle-payload"
        assert bundle.sha256 == hashlib.sha256(b"signed-bundle-payload").hexdigest()
        assert bundle.artifact_count == 2
        assert service.published_bundle(handle.operation_id, _actor(factory, admin_id))
        with pytest.raises(ExportOrchestrationError) as denied:
            service.published_bundle(handle.operation_id, _actor(factory, other_id))
        assert denied.value.code == "export_bundle_forbidden"

        package_inputs = bundles[-1].artifacts
        assert isinstance(package_inputs[0], ContainerImagePackageInput)
        assert package_inputs[0].repository == "project/app"
        assert package_inputs[0].payload_path == "images/artifact-0001"
        assert isinstance(package_inputs[1], HelmChartPackageInput)
        assert package_inputs[1].repository == "project/charts"
        assert package_inputs[1].name == "chart"
        assert package_inputs[1].payload_path == "charts/artifact-0002.tgz"
        assert skopeo.calls[0][0:2] == ("project/app", "1.0")
        assert helm.calls[0][0:3] == ("project/charts", "chart", "2.0.0")
        await manager.shutdown()

    asyncio.run(scenario())


def test_preview_then_changed_digest_blocks_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        factory, manager, user_id, _admin_id, _other_id = _environment(tmp_path, settings)
        artifacts = _artifacts()
        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: FakeHarborClient(artifacts),
        )
        preview = await service.preview(_request())
        assert preview.known_size_bytes == 3072
        artifacts[("project", "app", "1.0")] = HarborArtifact(
            digest=_CHANGED_DIGEST,
            type="IMAGE",
            size=1024,
        )
        with pytest.raises(ExportOrchestrationError) as exc:
            await service.start(_request(), _actor(factory, user_id))
        assert exc.value.code == "export_source_changed"
        assert list(settings.bundle_outgoing_root.glob("*")) == []

    asyncio.run(scenario())


def test_worker_detects_digest_change_and_marks_remaining_skipped(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        factory, manager, user_id, _admin_id, _other_id = _environment(tmp_path, settings)
        artifacts = _artifacts()
        calls = 0

        def harbor_factory() -> FakeHarborClient:
            nonlocal calls
            calls += 1
            if calls >= 2:
                changed = _artifacts()
                changed[("project", "app", "1.0")] = HarborArtifact(
                    digest=_CHANGED_DIGEST,
                    type="IMAGE",
                    size=1024,
                )
                return FakeHarborClient(changed)
            return FakeHarborClient(artifacts)

        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=harbor_factory,
            skopeo_factory=lambda _session, _settings: FakeSkopeo(),
            helm_factory=lambda _session, _settings: FakeHelm(),
            bundle_factory=lambda service_settings: FakeBundle(service_settings),
        )
        await manager.startup()
        handle, _delivery_id = await service.start(_request(), _actor(factory, user_id))
        await manager.wait(handle.operation_id)
        operation = _operation(factory, handle.operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.FAILED
        assert operation.error_code == "export_source_changed"
        assert [item.status for item in operation.artifacts] == [
            ArtifactStatus.FAILED,
            ArtifactStatus.SKIPPED,
        ]
        assert not settings.bundle_outgoing_root.exists()
        await manager.shutdown()

    asyncio.run(scenario())


def test_artifact_failure_is_fail_fast_and_does_not_publish(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        factory, manager, user_id, _admin_id, _other_id = _environment(tmp_path, settings)
        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: FakeHarborClient(_artifacts()),
            skopeo_factory=lambda _session, _settings: FakeSkopeo(fail=True),
            helm_factory=lambda _session, _settings: FakeHelm(),
            bundle_factory=lambda service_settings: FakeBundle(service_settings),
        )
        await manager.startup()
        handle, _delivery_id = await service.start(_request(), _actor(factory, user_id))
        await manager.wait(handle.operation_id)
        operation = _operation(factory, handle.operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.FAILED
        assert operation.error_code == "skopeo_command_failed"
        assert [item.status for item in operation.artifacts] == [
            ArtifactStatus.FAILED,
            ArtifactStatus.SKIPPED,
        ]
        assert not settings.bundle_outgoing_root.exists()
        await manager.shutdown()

    asyncio.run(scenario())


def test_package_self_verification_failure_does_not_publish(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        factory, manager, user_id, _admin_id, _other_id = _environment(tmp_path, settings)
        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: FakeHarborClient(_artifacts()),
            skopeo_factory=lambda _session, _settings: FakeSkopeo(),
            helm_factory=lambda _session, _settings: FakeHelm(),
            bundle_factory=lambda service_settings: FakeBundle(service_settings, fail=True),
        )
        await manager.startup()
        handle, _delivery_id = await service.start(_request(), _actor(factory, user_id))
        await manager.wait(handle.operation_id)
        operation = _operation(factory, handle.operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.FAILED
        assert operation.error_code == "bundle_signature_untrusted"
        assert not settings.bundle_outgoing_root.exists()
        await manager.shutdown()

    asyncio.run(scenario())


def test_existing_delivery_collision_is_not_deleted_on_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        factory, manager, user_id, _admin_id, _other_id = _environment(tmp_path, settings)
        settings.bundle_outgoing_root.mkdir(parents=True)
        archive = settings.bundle_outgoing_root / f"{_DELIVERY_ID}.htp.tar.gz"
        sidecar = settings.bundle_outgoing_root / f"{archive.name}.sha256"
        archive.write_bytes(b"pre-existing")
        digest = hashlib.sha256(b"pre-existing").hexdigest()
        sidecar.write_text(f"{digest}  {archive.name}\n")

        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: FakeHarborClient(_artifacts()),
            skopeo_factory=lambda _session, _settings: FakeSkopeo(),
            helm_factory=lambda _session, _settings: FakeHelm(),
            bundle_factory=lambda service_settings: FakeBundle(service_settings),
        )
        await manager.startup()
        handle, _delivery_id = await service.start(_request(), _actor(factory, user_id))
        await manager.wait(handle.operation_id)
        operation = _operation(factory, handle.operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.FAILED
        assert archive.read_bytes() == b"pre-existing"
        assert sidecar.read_text() == f"{digest}  {archive.name}\n"
        await manager.shutdown()

    asyncio.run(scenario())


def test_target_contour_rejects_before_harbor_access(tmp_path: Path) -> None:
    settings = _settings(tmp_path, contour=PortalContour.TARGET)
    factory, manager, _user_id, _admin_id, _other_id = _environment(tmp_path, settings)
    calls = 0

    def harbor_factory() -> FakeHarborClient:
        nonlocal calls
        calls += 1
        return FakeHarborClient(_artifacts())

    service = ExportOrchestrationService(
        factory,
        settings,
        manager,
        harbor_client_factory=harbor_factory,
    )
    with pytest.raises(ExportOrchestrationError) as exc:
        asyncio.run(service.preview(_request()))
    assert exc.value.code == "export_wrong_contour"
    assert calls == 0


def test_cancellation_during_publication_removes_worker_owned_delivery(tmp_path: Path) -> None:
    async def scenario() -> None:
        import threading

        settings = _settings(tmp_path)
        factory, manager, user_id, _admin_id, _other_id = _environment(tmp_path, settings)
        service = ExportOrchestrationService(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: FakeHarborClient(_artifacts()),
            skopeo_factory=lambda _session, _settings: FakeSkopeo(),
            helm_factory=lambda _session, _settings: FakeHelm(),
            bundle_factory=lambda service_settings: FakeBundle(service_settings),
        )
        published = threading.Event()
        release = threading.Event()
        original_publish = service._publish_staged_bundle

        def blocking_publish(build, final_archive, final_sidecar, publication) -> None:
            original_publish(build, final_archive, final_sidecar, publication)
            published.set()
            assert release.wait(timeout=5)

        service._publish_staged_bundle = blocking_publish  # type: ignore[method-assign]
        await manager.startup()
        handle, _delivery_id = await service.start(_request(), _actor(factory, user_id))
        assert await asyncio.to_thread(published.wait, 5)

        cancel_task = asyncio.create_task(manager.cancel(handle.operation_id))
        await asyncio.sleep(0)
        release.set()
        await cancel_task

        operation = _operation(factory, handle.operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.CANCELLED
        archive = settings.bundle_outgoing_root / f"{_DELIVERY_ID}.htp.tar.gz"
        sidecar = settings.bundle_outgoing_root / f"{archive.name}.sha256"
        assert not archive.exists()
        assert not sidecar.exists()
        await manager.shutdown()

    asyncio.run(scenario())
