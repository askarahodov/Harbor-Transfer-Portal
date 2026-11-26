from __future__ import annotations

import errno
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.schemas.imports import ImportArtifactPreviewResponse, ImportPreviewResponse
from app.services.import_persistence import ImportOperationPersistence
from app.services.operation_manager import OperationManager, OperationTaskFailure

BUNDLE_SHA = "a" * 64
SIGNER_A = "sha256:" + "b" * 64
SIGNER_B = "sha256:" + "c" * 64
DIGEST = "sha256:" + "d" * 64


def environment(tmp_path: Path) -> tuple[ImportOperationPersistence, OperationManager]:
    data = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.TARGET,
        database_url=f"sqlite:///{tmp_path / 'import-persistence.db'}",
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        import_receipt_root=data / "receipts" / "imports",
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    persistence = ImportOperationPersistence(
        session_factory=session_factory,
        receipt_root=settings.import_receipt_root.resolve(),
    )
    return persistence, manager


def create_import(manager: OperationManager) -> int:
    return manager.create_operation(
        operation_type=OperationType.IMPORT,
        actor_user_id=None,
        actor_username="target-operator",
        initial_status=OperationStatus.UPLOADED,
    )


def preview(operation_id: int) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        operation_id=operation_id,
        status=OperationStatus.READY,
        source_delivery_id="DELIVERY-20260922-ABCDEF",
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=4096,
        signing_key_fingerprint=SIGNER_A,
        verified_at=datetime(2026, 9, 22, 8, 0, tzinfo=UTC),
        checksum_verified=True,
        signature_verified=True,
        schema_verified=True,
        artifacts=[
            ImportArtifactPreviewResponse(
                index=0,
                artifact_type="container-image",
                repository="team/app",
                reference="1.0.0",
                expected_digest=DIGEST,
                payload_size=1024,
                classification=ImportPreviewState.NEW,
            )
        ],
    )


def test_load_execution_state_rejects_incomplete_persisted_state(tmp_path: Path) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)

    with pytest.raises(OperationTaskFailure) as captured:
        persistence.load_execution_state(operation_id)

    assert captured.value.code == "import_execution_state_invalid"


def test_verified_signer_cannot_drift_within_operation(tmp_path: Path) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)

    persistence.persist_verified_signer(operation_id, SIGNER_A)

    with pytest.raises(OperationTaskFailure) as captured:
        persistence.persist_verified_signer(operation_id, SIGNER_B)

    assert captured.value.code == "import_signing_key_changed"


def test_preview_is_persisted_once_and_execution_state_round_trips(tmp_path: Path) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    expected_preview = preview(operation_id)
    persistence.persist_preview(operation_id, expected_preview)

    requested_at = datetime(2026, 9, 22, 8, 5, tzinfo=UTC)
    with manager.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.import_policy_json = json.dumps(
            {
                "overwrite_conflicts": False,
                "skip_conflicts": True,
                "requested_at": requested_at.isoformat(),
            }
        )
        session.commit()

    (
        operation,
        loaded_preview,
        overwrite,
        skip_conflicts,
        loaded_requested_at,
    ) = persistence.load_execution_state(operation_id)

    assert operation.id == operation_id
    assert loaded_preview == expected_preview
    assert overwrite is False
    assert skip_conflicts is True
    assert loaded_requested_at == requested_at

    with pytest.raises(OperationTaskFailure) as captured:
        persistence.persist_preview(operation_id, expected_preview)

    assert captured.value.code == "import_preview_already_persisted"


def test_receipt_file_is_immutable(tmp_path: Path) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    expected_preview = preview(operation_id)
    persistence.persist_preview(operation_id, expected_preview)
    requested_at = datetime(2026, 9, 22, 8, 5, tzinfo=UTC)

    persistence.write_receipt(
        operation_id,
        expected_preview,
        overwrite=False,
        requested_at=requested_at,
        failures=0,
    )

    receipt_path = persistence.receipt_root / f"import-{operation_id}.json"
    assert receipt_path.is_file()
    first_payload = receipt_path.read_text(encoding="utf-8")
    assert '"result": "COMPLETED"' in first_payload

    with pytest.raises(OperationTaskFailure) as captured:
        persistence.write_receipt(
            operation_id,
            expected_preview,
            overwrite=False,
            requested_at=requested_at,
            failures=0,
        )

    assert captured.value.code == "import_receipt_exists"
    assert receipt_path.read_text(encoding="utf-8") == first_payload


def test_receipt_write_failure_leaves_no_file_or_database_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    expected_preview = preview(operation_id)

    def fail_sync(_fd: int) -> None:
        raise OSError("receipt fsync failed")

    monkeypatch.setattr("app.services.import_persistence.os.fsync", fail_sync)
    with pytest.raises(OperationTaskFailure) as captured:
        persistence.write_receipt(
            operation_id,
            expected_preview,
            overwrite=False,
            requested_at=datetime(2026, 9, 22, 8, 5, tzinfo=UTC),
            failures=0,
        )

    assert captured.value.code == "import_receipt_publish_failed"
    assert list(persistence.receipt_root.iterdir()) == []
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.import_receipt_json is None


def test_receipt_directory_fsync_failure_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    expected_preview = preview(operation_id)

    def fail_directory_sync(_path: Path) -> None:
        raise OSError("receipt directory fsync failed")

    monkeypatch.setattr(
        ImportOperationPersistence, "_fsync_directory", staticmethod(fail_directory_sync)
    )
    with pytest.raises(OperationTaskFailure) as captured:
        persistence.write_receipt(
            operation_id,
            expected_preview,
            overwrite=False,
            requested_at=datetime(2026, 9, 22, 8, 5, tzinfo=UTC),
            failures=0,
        )

    assert captured.value.code == "import_receipt_publish_failed"
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.import_receipt_json is None


def test_receipt_hardlink_failure_without_collision_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    expected_preview = preview(operation_id)

    def unsupported_link(_source: object, _destination: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlink not supported")

    monkeypatch.setattr("app.services.import_persistence.os.link", unsupported_link)
    with pytest.raises(OperationTaskFailure) as captured:
        persistence.write_receipt(
            operation_id,
            expected_preview,
            overwrite=False,
            requested_at=datetime(2026, 9, 22, 8, 5, tzinfo=UTC),
            failures=0,
        )

    assert captured.value.code == "import_receipt_publish_failed"
    assert list(persistence.receipt_root.iterdir()) == []
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.import_receipt_json is None


def _publish_receipt(persistence: ImportOperationPersistence, operation_id: int) -> None:
    persistence.write_receipt(
        operation_id,
        preview(operation_id),
        overwrite=False,
        requested_at=datetime(2026, 9, 22, 8, 5, tzinfo=UTC),
        failures=0,
    )


def test_publish_receipt_removes_stale_matching_temp_files(tmp_path: Path) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    root = persistence.receipt_root
    root.mkdir(parents=True, exist_ok=True)
    stale = root / f".import-{operation_id}.json.abandoned.tmp"
    stale.write_text("partial", encoding="utf-8")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    _publish_receipt(persistence, operation_id)

    assert not stale.exists()
    assert (root / f"import-{operation_id}.json").is_file()
    assert [path.name for path in root.iterdir() if path.name.endswith(".tmp")] == []


def test_publish_receipt_preserves_fresh_or_foreign_temp_files(tmp_path: Path) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)
    root = persistence.receipt_root
    root.mkdir(parents=True, exist_ok=True)
    fresh = root / f".import-{operation_id}.json.inflight.tmp"
    fresh.write_text("live", encoding="utf-8")
    other_destination = root / f".import-{operation_id + 1}.json.abandoned.tmp"
    other_destination.write_text("other", encoding="utf-8")
    other_suffix = root / ".import-misc-temp.tmp"
    other_suffix.write_text("suffix", encoding="utf-8")
    old = time.time() - 7200
    os.utime(other_destination, (old, old))
    os.utime(other_suffix, (old, old))
    target = root / "symlink-target"
    target.write_text("target", encoding="utf-8")
    symlink = root / f".import-{operation_id}.json.link.tmp"
    symlink.symlink_to(target)
    os.utime(symlink, (old, old), follow_symlinks=False)

    _publish_receipt(persistence, operation_id)

    assert fresh.exists()
    assert other_destination.exists()
    assert other_suffix.exists()
    assert symlink.is_symlink()
    assert target.exists()


def test_publish_receipt_survives_stale_temp_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence, manager = environment(tmp_path)
    operation_id = create_import(manager)

    def denied_scan(_path: object) -> object:
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr("app.services.import_persistence.os.scandir", denied_scan)

    _publish_receipt(persistence, operation_id)

    assert (persistence.receipt_root / f"import-{operation_id}.json").is_file()
