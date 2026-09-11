import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services.operation_manager import OperationContext, OperationManager


def _manager(tmp_path: Path, *, max_concurrent: int = 1) -> OperationManager:
    database_url = f"sqlite:///{tmp_path / 'queued-recovery.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        operation_workspace_root=tmp_path / "work",
        operation_max_concurrent=max_concurrent,
        operation_disk_reserve_bytes=0,
        operation_shutdown_timeout_seconds=2,
    )
    return OperationManager(create_session_factory(engine), settings)


def _operation(manager: OperationManager, operation_id: int) -> Operation:
    with manager.session_factory() as session:
        operation = session.scalar(
            select(Operation)
            .where(Operation.id == operation_id)
            .options(selectinload(Operation.artifacts))
        )
        assert operation is not None
        _ = operation.artifacts
        session.expunge_all()
        return operation


@pytest.mark.parametrize(
    ("operation_type", "initial_status"),
    [
        (OperationType.EXPORT, OperationStatus.CREATED),
        (OperationType.IMPORT, OperationStatus.UPLOADED),
        (OperationType.IMPORT, OperationStatus.DISCOVERED),
    ],
)
def test_restart_fails_claimed_worker_before_first_transition(
    tmp_path: Path,
    operation_type: OperationType,
    initial_status: OperationStatus,
) -> None:
    manager = _manager(tmp_path)
    operation_id = manager.create_operation(
        operation_type=operation_type,
        actor_user_id=None,
        actor_username="operator",
        initial_status=initial_status,
    )
    workspace = manager.prepare_workspace(operation_id)
    (workspace / "queued-marker").write_text("queued", encoding="utf-8")

    now = datetime.now(UTC)
    with manager.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.worker_token = "stale-queued-worker"
        operation.worker_started_at = now
        operation.heartbeat_at = now
        session.commit()

    assert manager.reconcile_interrupted_operations() == 1

    recovered = _operation(manager, operation_id)
    assert recovered.status is OperationStatus.FAILED
    assert recovered.error_code == "operation_interrupted_restart"
    assert recovered.worker_token is None
    assert recovered.worker_started_at is None
    assert recovered.heartbeat_at is None
    assert not workspace.exists()


def test_shutdown_fails_worker_cancelled_while_waiting_for_slot(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_concurrent=1)
    first_id = manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=None,
        actor_username="first",
    )
    queued_id = manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=None,
        actor_username="queued",
    )

    async def scenario() -> None:
        first_started = asyncio.Event()
        queued_started = asyncio.Event()

        async def first_worker(context: OperationContext) -> None:
            context.transition(OperationStatus.VALIDATING)
            first_started.set()
            await asyncio.Event().wait()

        async def queued_worker(_context: OperationContext) -> None:
            queued_started.set()

        await manager.startup()
        manager.submit(first_id, first_worker)
        await first_started.wait()
        manager.submit(queued_id, queued_worker)
        await asyncio.sleep(0)

        queued_before_shutdown = _operation(manager, queued_id)
        assert queued_before_shutdown.status is OperationStatus.CREATED
        assert queued_before_shutdown.worker_token is not None
        assert not queued_started.is_set()

        await manager.shutdown()
        assert not queued_started.is_set()

    asyncio.run(scenario())

    first = _operation(manager, first_id)
    queued = _operation(manager, queued_id)
    assert first.status is OperationStatus.FAILED
    assert first.error_code == "operation_interrupted_shutdown"
    assert queued.status is OperationStatus.FAILED
    assert queued.error_code == "operation_interrupted_shutdown"
    assert queued.worker_token is None
