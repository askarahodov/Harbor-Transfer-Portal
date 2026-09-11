from pathlib import Path

from app.config import Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services.export_recovery import reconcile_incomplete_export_publications


def _operation(
    session_factory,  # type: ignore[no-untyped-def]
    *,
    delivery_id: str,
    status: OperationStatus,
) -> int:
    with session_factory() as session:
        operation = Operation(
            delivery_id=delivery_id,
            type=OperationType.EXPORT,
            status=status,
            actor_username="operator",
            progress_current=0,
            progress_total=0,
            total_artifacts=0,
            successful_artifacts=0,
            failed_artifacts=0,
            skipped_artifacts=0,
            conflict_artifacts=0,
            bundle_filename=f"{delivery_id}.htp.tar.gz",
            bundle_sha256="a" * 64,
            bundle_size_bytes=7,
        )
        session.add(operation)
        session.commit()
        return operation.id


def test_startup_cleanup_removes_only_non_completed_export_publications(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'recovery.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    outgoing = tmp_path / "data" / "outgoing"
    outgoing.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        bundle_outgoing_root=outgoing,
    )

    failed_delivery = "DELIVERY-20260911-FAILED01"
    completed_delivery = "DELIVERY-20260911-DONE0001"
    failed_id = _operation(
        session_factory,
        delivery_id=failed_delivery,
        status=OperationStatus.VERIFYING,
    )
    completed_id = _operation(
        session_factory,
        delivery_id=completed_delivery,
        status=OperationStatus.COMPLETED,
    )

    for delivery in (failed_delivery, completed_delivery):
        archive = outgoing / f"{delivery}.htp.tar.gz"
        archive.write_bytes(b"payload")
        (outgoing / f"{archive.name}.sha256").write_text(
            f"{'a' * 64}  {archive.name}\n",
            encoding="utf-8",
        )

    cleaned = reconcile_incomplete_export_publications(session_factory, settings)
    assert cleaned == 1
    assert not (outgoing / f"{failed_delivery}.htp.tar.gz").exists()
    assert not (outgoing / f"{failed_delivery}.htp.tar.gz.sha256").exists()
    assert (outgoing / f"{completed_delivery}.htp.tar.gz").exists()
    assert (outgoing / f"{completed_delivery}.htp.tar.gz.sha256").exists()

    with session_factory() as session:
        failed = session.get(Operation, failed_id)
        completed = session.get(Operation, completed_id)
        assert failed is not None and completed is not None
        assert failed.bundle_filename is None
        assert failed.bundle_sha256 is None
        assert failed.bundle_size_bytes is None
        assert completed.bundle_filename == f"{completed_delivery}.htp.tar.gz"
