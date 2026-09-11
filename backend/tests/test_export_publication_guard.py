import hashlib
from pathlib import Path

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services import export_publication_guard
from app.services.bundle_package_service import BundlePackageError
from app.services.export_recovery import reconcile_incomplete_export_publications
from app.services.operation_manager import OperationManager


DELIVERY_ID = "DELIVERY-20260911-OWNERSHIP01"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'ownership.db'}",
        operation_workspace_root=tmp_path / "operations",
        operation_disk_reserve_bytes=0,
        bundle_payload_root=tmp_path,
        bundle_temp_root=tmp_path / "bundle-temp",
        bundle_outgoing_root=tmp_path / "outgoing",
    )


def _orchestrator(tmp_path: Path) -> tuple[
    export_publication_guard.PublicationSafeExportOrchestrator,
    object,
    Settings,
]:
    settings = _settings(tmp_path)
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    return (
        export_publication_guard.PublicationSafeExportOrchestrator(
            session_factory,
            settings,
            manager,
        ),
        session_factory,
        settings,
    )


def _operation(session_factory, *, metadata: bool = False) -> int:  # type: ignore[no-untyped-def]
    with session_factory() as session:
        operation = Operation(
            delivery_id=DELIVERY_ID,
            type=OperationType.EXPORT,
            status=OperationStatus.PACKAGING,
            actor_username="operator",
            progress_current=0,
            progress_total=0,
            total_artifacts=0,
            successful_artifacts=0,
            failed_artifacts=0,
            skipped_artifacts=0,
            conflict_artifacts=0,
            bundle_filename=f"{DELIVERY_ID}.htp.tar.gz" if metadata else None,
            bundle_sha256="a" * 64 if metadata else None,
            bundle_size_bytes=7 if metadata else None,
        )
        session.add(operation)
        session.commit()
        return operation.id


def test_atomic_publish_does_not_replace_preexisting_archive(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.bundle_outgoing_root.mkdir(parents=True)
    source = tmp_path / "new.htp.tar.gz"
    source.write_bytes(b"new")
    final_archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    final_sidecar = settings.bundle_outgoing_root / f"{final_archive.name}.sha256"
    final_archive.write_bytes(b"foreign")
    recorded: list[str] = []

    service = export_publication_guard._OwnedBundlePackageService(
        settings,
        on_publication_recorded=lambda delivery_id, _name, _sha, _size: recorded.append(
            delivery_id
        ),
        on_publication_rolled_back=lambda _delivery_id: None,
    )

    with pytest.raises(BundlePackageError) as exc:
        service._atomic_publish(
            source,
            final_archive,
            final_sidecar,
            hashlib.sha256(b"new").hexdigest(),
        )

    assert exc.value.code == "bundle_delivery_exists"
    assert final_archive.read_bytes() == b"foreign"
    assert not final_sidecar.exists()
    assert recorded == []


def test_sidecar_collision_rolls_back_only_owned_archive(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.bundle_outgoing_root.mkdir(parents=True)
    source = tmp_path / "new.htp.tar.gz"
    source.write_bytes(b"new")
    final_archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    final_sidecar = settings.bundle_outgoing_root / f"{final_archive.name}.sha256"
    final_sidecar.write_text("foreign-sidecar", encoding="utf-8")
    recorded: list[str] = []
    rolled_back: list[str] = []

    service = export_publication_guard._OwnedBundlePackageService(
        settings,
        on_publication_recorded=lambda delivery_id, _name, _sha, _size: recorded.append(
            delivery_id
        ),
        on_publication_rolled_back=rolled_back.append,
    )

    with pytest.raises(BundlePackageError) as exc:
        service._atomic_publish(
            source,
            final_archive,
            final_sidecar,
            hashlib.sha256(b"new").hexdigest(),
        )

    assert exc.value.code == "bundle_delivery_exists"
    assert not final_archive.exists()
    assert final_sidecar.read_text(encoding="utf-8") == "foreign-sidecar"
    assert recorded == [DELIVERY_ID]
    assert rolled_back == [DELIVERY_ID]


def test_orchestrator_cleanup_preserves_unowned_preexisting_delivery(tmp_path: Path) -> None:
    orchestrator, session_factory, settings = _orchestrator(tmp_path)
    _operation(session_factory)
    settings.bundle_outgoing_root.mkdir(parents=True)
    archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = settings.bundle_outgoing_root / f"{archive.name}.sha256"
    archive.write_bytes(b"foreign")
    sidecar.write_text("foreign", encoding="utf-8")

    orchestrator._cleanup_published_delivery(DELIVERY_ID)

    assert archive.read_bytes() == b"foreign"
    assert sidecar.read_text(encoding="utf-8") == "foreign"


def test_owned_cleanup_removes_files_and_metadata(tmp_path: Path) -> None:
    orchestrator, session_factory, settings = _orchestrator(tmp_path)
    operation_id = _operation(session_factory)
    settings.bundle_outgoing_root.mkdir(parents=True)
    archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = settings.bundle_outgoing_root / f"{archive.name}.sha256"
    archive.write_bytes(b"owned")
    sha256 = hashlib.sha256(b"owned").hexdigest()
    sidecar.write_text(f"{sha256}  {archive.name}\n", encoding="utf-8")

    orchestrator._record_owned_publication(
        DELIVERY_ID,
        archive.name,
        sha256,
        archive.stat().st_size,
    )
    orchestrator._cleanup_published_delivery(DELIVERY_ID)

    assert not archive.exists()
    assert not sidecar.exists()
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.bundle_filename is None
        assert operation.bundle_sha256 is None
        assert operation.bundle_size_bytes is None


def test_recovery_preserves_unowned_preexisting_delivery(tmp_path: Path) -> None:
    _orchestrator_instance, session_factory, settings = _orchestrator(tmp_path)
    _operation(session_factory)
    settings.bundle_outgoing_root.mkdir(parents=True)
    archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = settings.bundle_outgoing_root / f"{archive.name}.sha256"
    archive.write_bytes(b"foreign")
    sidecar.write_text("foreign", encoding="utf-8")

    cleaned = reconcile_incomplete_export_publications(session_factory, settings)

    assert cleaned == 0
    assert archive.read_bytes() == b"foreign"
    assert sidecar.read_text(encoding="utf-8") == "foreign"


def test_recovery_removes_persisted_owned_publication(tmp_path: Path) -> None:
    _orchestrator_instance, session_factory, settings = _orchestrator(tmp_path)
    operation_id = _operation(session_factory, metadata=True)
    settings.bundle_outgoing_root.mkdir(parents=True)
    archive = settings.bundle_outgoing_root / f"{DELIVERY_ID}.htp.tar.gz"
    sidecar = settings.bundle_outgoing_root / f"{archive.name}.sha256"
    archive.write_bytes(b"payload")
    sidecar.write_text(f"{'a' * 64}  {archive.name}\n", encoding="utf-8")

    cleaned = reconcile_incomplete_export_publications(session_factory, settings)

    assert cleaned == 1
    assert not archive.exists()
    assert not sidecar.exists()
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.bundle_filename is None
        assert operation.bundle_sha256 is None
        assert operation.bundle_size_bytes is None
