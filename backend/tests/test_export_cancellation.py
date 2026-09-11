import asyncio
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.domain.artifacts import ArtifactKind
from app.domain.bundle import OperationStatus
from app.schemas.exports import ExportArtifactSelection
from app.services.export_orchestrator import ExportOrchestrator
from app.services.harbor_client import HarborArtifact
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import ExportResult

DIGEST = "sha256:" + "a" * 64
DELIVERY_ID = "DELIVERY-20260911-CANCEL12"


class _Harbor:
    def get_artifact(self, _project: str, _repository: str, _reference: str) -> HarborArtifact:
        return HarborArtifact(digest=DIGEST, type="IMAGE", size=32)

    def close(self) -> None:
        return None


class _Skopeo:
    async def export_image(self, _image, destination: Path) -> ExportResult:  # type: ignore[no-untyped-def]
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "oci-layout").write_text("{}", encoding="utf-8")
        return ExportResult(
            source_digest=DIGEST,
            payload_digest=DIGEST,
            payload_path=destination,
        )


class _BlockingPackage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started = threading.Event()
        self.release = threading.Event()

    def allocate_delivery_id(self) -> str:
        return DELIVERY_ID

    def build_bundle(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.started.set()
        assert self.release.wait(timeout=5)
        root = self.settings.bundle_outgoing_root
        root.mkdir(parents=True, exist_ok=True)
        archive = root / f"{DELIVERY_ID}.htp.tar.gz"
        sidecar = root / f"{DELIVERY_ID}.htp.tar.gz.sha256"
        archive.write_bytes(b"published-after-cancel-request")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        return SimpleNamespace(
            manifest=SimpleNamespace(delivery_id=DELIVERY_ID),
            archive_path=archive,
            sidecar_path=sidecar,
            archive_sha256=digest,
            archive_size=archive.stat().st_size,
        )


def test_cancel_during_packaging_waits_and_removes_ready_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'cancel.db'}",
        harbor_url="https://harbor.local",
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        operation_shutdown_timeout_seconds=2,
        skopeo_payload_root=data,
        helm_workspace_root=data / "packages",
        bundle_payload_root=data,
        bundle_outgoing_root=data / "outgoing",
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    manager = OperationManager(create_session_factory(engine), settings)
    harbor = _Harbor()
    package = _BlockingPackage(settings)
    orchestrator = ExportOrchestrator(
        manager.session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        skopeo_factory=lambda _session: _Skopeo(),
        package_factory=lambda: package,
    )
    selection = ExportArtifactSelection(
        kind=ArtifactKind.CONTAINER_IMAGE,
        project="project-a",
        repository="apps/demo",
        reference="1.0.0",
        digest=DIGEST,
    )

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.start_export(
            (selection,),
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="operator",
            comment=None,
        )
        assert await asyncio.to_thread(package.started.wait, 5)
        cancel_task = asyncio.create_task(manager.cancel(started.operation_id))
        await asyncio.sleep(0.05)
        assert not cancel_task.done()
        package.release.set()
        await cancel_task
        await manager.shutdown()
        return started.operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.CANCELLED
    assert operation.bundle_filename is None
    archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz.sha256"
    assert not archive.exists()
    assert not sidecar.exists()
