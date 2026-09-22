from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services.storage_retention import cleanup_transfer_storage


def _session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'retention.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    return database_url, create_session_factory(engine)


def _operation(
    session_factory,  # type: ignore[no-untyped-def]
    *,
    operation_type: OperationType,
    status: OperationStatus,
    finished_at: datetime | None = None,
    delivery_id: str | None = None,
    storage_key: str | None = None,
) -> int:
    with session_factory() as session:
        operation = Operation(
            type=operation_type,
            status=status,
            actor_username="operator",
            delivery_id=delivery_id,
            import_storage_key=storage_key,
            finished_at=finished_at,
        )
        session.add(operation)
        session.commit()
        return operation.id


def test_retention_prunes_only_expired_terminal_payloads(tmp_path: Path) -> None:
    database_url, session_factory = _session_factory(tmp_path)
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=8)
    recent = now - timedelta(days=2)
    outgoing = tmp_path / "data" / "outgoing"
    staging = tmp_path / "data" / "incoming" / "staged"
    extracted = tmp_path / "data" / "incoming" / "verified"
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        bundle_outgoing_root=outgoing,
        import_staging_root=staging,
        bundle_extract_root=extracted,
        export_bundle_retention_seconds=7 * 24 * 60 * 60,
        import_bundle_retention_seconds=7 * 24 * 60 * 60,
    )

    expired_delivery = "DELIVERY-20260901-EXPIRED1"
    recent_delivery = "DELIVERY-20260920-RECENT01"
    active_delivery = "DELIVERY-20260901-ACTIVE01"
    _operation(
        session_factory,
        operation_type=OperationType.EXPORT,
        status=OperationStatus.COMPLETED,
        finished_at=old,
        delivery_id=expired_delivery,
    )
    _operation(
        session_factory,
        operation_type=OperationType.EXPORT,
        status=OperationStatus.COMPLETED,
        finished_at=recent,
        delivery_id=recent_delivery,
    )
    _operation(
        session_factory,
        operation_type=OperationType.EXPORT,
        status=OperationStatus.RUNNING,
        finished_at=old,
        delivery_id=active_delivery,
    )

    outgoing.mkdir(parents=True)
    for delivery in (expired_delivery, recent_delivery, active_delivery):
        (outgoing / f"{delivery}.htp.tar.gz").write_bytes(b"bundle")
        (outgoing / f"{delivery}.htp.tar.gz.sha256").write_bytes(b"checksum")
        (outgoing / f"{delivery}.htp-handoff.json").write_bytes(b"handoff")

    expired_key = "a" * 48
    protected_key = "b" * 48
    _operation(
        session_factory,
        operation_type=OperationType.IMPORT,
        status=OperationStatus.FAILED,
        finished_at=old,
        storage_key=expired_key,
    )
    _operation(
        session_factory,
        operation_type=OperationType.IMPORT,
        status=OperationStatus.FAILED,
        finished_at=old,
        storage_key=protected_key,
    )
    active_import_id = _operation(
        session_factory,
        operation_type=OperationType.IMPORT,
        status=OperationStatus.READY,
        storage_key=protected_key,
    )
    terminal_import_id = _operation(
        session_factory,
        operation_type=OperationType.IMPORT,
        status=OperationStatus.FAILED,
        finished_at=recent,
        storage_key="c" * 48,
    )

    for key in (expired_key, protected_key, "c" * 48):
        path = staging / key
        path.mkdir(parents=True)
        (path / "bundle.htp.tar.gz").write_bytes(b"payload")

    terminal_extract = extracted / f"import-{terminal_import_id}"
    active_extract = extracted / f"import-{active_import_id}"
    orphan_extract = extracted / "import-999999"
    for path in (terminal_extract, active_extract, orphan_extract):
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"extracted")

    report = cleanup_transfer_storage(session_factory, settings, now=now)

    assert report.export_deliveries_removed == 1
    assert report.import_staging_removed == 1
    assert report.extraction_workspaces_removed == 2
    assert report.bytes_reclaimed > 0

    assert not (outgoing / f"{expired_delivery}.htp.tar.gz").exists()
    assert not (outgoing / f"{expired_delivery}.htp.tar.gz.sha256").exists()
    assert not (outgoing / f"{expired_delivery}.htp-handoff.json").exists()
    assert (outgoing / f"{recent_delivery}.htp.tar.gz").exists()
    assert (outgoing / f"{active_delivery}.htp.tar.gz").exists()

    assert not (staging / expired_key).exists()
    assert (staging / protected_key).exists()
    assert (staging / ("c" * 48)).exists()

    assert not terminal_extract.exists()
    assert not orphan_extract.exists()
    assert active_extract.exists()


def test_retention_ignores_unsafe_storage_keys_and_unexpected_paths(tmp_path: Path) -> None:
    database_url, session_factory = _session_factory(tmp_path)
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=30)
    staging = tmp_path / "data" / "incoming" / "staged"
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        import_staging_root=staging,
        import_bundle_retention_seconds=60 * 60,
    )
    _operation(
        session_factory,
        operation_type=OperationType.IMPORT,
        status=OperationStatus.FAILED,
        finished_at=old,
        storage_key="../outside",
    )
    unexpected = staging / "manual-data"
    unexpected.mkdir(parents=True)
    (unexpected / "keep.txt").write_text("keep", encoding="utf-8")

    report = cleanup_transfer_storage(session_factory, settings, now=now)

    assert report.import_staging_removed == 0
    assert unexpected.is_dir()
