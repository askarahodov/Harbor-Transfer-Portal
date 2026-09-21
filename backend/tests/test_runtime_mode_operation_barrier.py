from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation, User, UserRole
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services.correlated_operation_manager import CorrelatedOperationManager
from app.services.operation_manager import OperationManager, OperationManagerError
from app.services.runtime_mode import RuntimeModeError, RuntimeModeService


def _environment(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'runtime-barrier.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        portal_contour=PortalContour.SOURCE,
        operation_workspace_root=tmp_path / "work",
        operation_disk_reserve_bytes=0,
    )
    with session_factory() as session:
        actor = User(
            username="admin",
            password_hash="synthetic-hash",
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(actor)
        session.commit()
        actor_id = actor.id
        RuntimeModeService(session, settings).initialize()
    manager = CorrelatedOperationManager(session_factory, settings)
    return settings, session_factory, manager, actor_id


def _switch(
    session_factory,  # type: ignore[no-untyped-def]
    settings: Settings,
    actor_id: int,
    target: PortalContour,
):  # type: ignore[no-untyped-def]
    with session_factory() as session:
        actor = session.get(User, actor_id)
        assert actor is not None
        return RuntimeModeService(session, settings).switch(target, actor=actor)


def test_runtime_mode_revision_and_operation_snapshot(tmp_path: Path) -> None:
    settings, session_factory, manager, actor_id = _environment(tmp_path)

    with session_factory() as session:
        initial = RuntimeModeService(session, settings).current_snapshot()
    assert initial.mode is PortalContour.SOURCE
    assert initial.version == 1

    switched = _switch(session_factory, settings, actor_id, PortalContour.TARGET)
    assert switched.previous is PortalContour.SOURCE
    assert switched.current is PortalContour.TARGET
    assert switched.mode_version == 2

    operation_id = manager.create_operation(
        operation_type=OperationType.IMPORT,
        actor_user_id=actor_id,
        actor_username="admin",
        initial_status=OperationStatus.UPLOADED,
    )
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.runtime_mode == "TARGET"
        assert operation.runtime_mode_version == 2

    with pytest.raises(RuntimeModeError) as exc_info:
        manager.create_operation(
            operation_type=OperationType.EXPORT,
            actor_user_id=actor_id,
            actor_username="admin",
        )
    assert exc_info.value.code == "runtime_mode_mismatch"


def test_operation_start_wins_race_and_switch_observes_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, session_factory, manager, actor_id = _environment(tmp_path)
    entered_create = Event()
    release_create = Event()
    original_create = OperationManager.create_operation

    def blocking_create(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        entered_create.set()
        assert release_create.wait(timeout=5)
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(OperationManager, "create_operation", blocking_create)

    with ThreadPoolExecutor(max_workers=2) as pool:
        start_future = pool.submit(
            manager.create_operation,
            operation_type=OperationType.EXPORT,
            actor_user_id=actor_id,
            actor_username="admin",
        )
        assert entered_create.wait(timeout=5)

        switch_future = pool.submit(
            _switch,
            session_factory,
            settings,
            actor_id,
            PortalContour.TARGET,
        )
        # The switch cannot pass the same process-wide barrier while operation
        # creation is deliberately paused inside it.
        assert not switch_future.done()
        release_create.set()
        operation_id = start_future.result(timeout=5)

        with pytest.raises(RuntimeModeError) as exc_info:
            switch_future.result(timeout=5)
        assert exc_info.value.code == "runtime_mode_busy"

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.runtime_mode == "SOURCE"
        assert operation.runtime_mode_version == 1
        snapshot = RuntimeModeService(session, settings).current_snapshot()
        assert snapshot.mode is PortalContour.SOURCE
        assert snapshot.version == 1


def test_switch_wins_then_old_mode_operation_cannot_start(tmp_path: Path) -> None:
    settings, session_factory, manager, actor_id = _environment(tmp_path)

    switched = _switch(session_factory, settings, actor_id, PortalContour.TARGET)
    assert switched.mode_version == 2

    with pytest.raises(RuntimeModeError) as exc_info:
        manager.create_operation(
            operation_type=OperationType.EXPORT,
            actor_user_id=actor_id,
            actor_username="admin",
        )
    assert exc_info.value.code == "runtime_mode_mismatch"

    operation_id = manager.create_operation(
        operation_type=OperationType.IMPORT,
        actor_user_id=actor_id,
        actor_username="admin",
        initial_status=OperationStatus.DISCOVERED,
    )
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.runtime_mode == "TARGET"
        assert operation.runtime_mode_version == 2


def test_submit_backfills_legacy_snapshot_only_in_compatible_mode(tmp_path: Path) -> None:
    settings, session_factory, manager, actor_id = _environment(tmp_path)
    with session_factory() as session:
        legacy = Operation(
            type=OperationType.EXPORT,
            status=OperationStatus.CREATED,
            actor_user_id=actor_id,
            actor_username="admin",
        )
        session.add(legacy)
        session.commit()
        operation_id = legacy.id

    async def no_op(_context) -> None:  # type: ignore[no-untyped-def]
        return None

    # submit() requires an event loop, but snapshot backfill happens before task creation.
    with pytest.raises(OperationManagerError) as exc_info:
        manager.submit(operation_id, no_op)
    assert exc_info.value.code == "operation_event_loop_required"

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.runtime_mode == "SOURCE"
        assert operation.runtime_mode_version == 1


def test_pending_switch_blocks_new_operation_start_and_completes_after_cancel(
    tmp_path: Path,
) -> None:
    settings, session_factory, manager, actor_id = _environment(tmp_path)
    operation_id = manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=actor_id,
        actor_username="admin",
    )

    with session_factory() as session:
        actor = session.get(User, actor_id)
        assert actor is not None
        runtime = RuntimeModeService(session, settings)
        preparation = runtime.begin_switch(PortalContour.TARGET)
        assert preparation.blocking_operation_ids == (operation_id,)

        try:
            with pytest.raises(RuntimeModeError) as exc_info:
                manager.create_operation(
                    operation_type=OperationType.EXPORT,
                    actor_user_id=actor_id,
                    actor_username="admin",
                )
            assert exc_info.value.code == "runtime_mode_switch_in_progress"

            asyncio.run(manager.cancel(operation_id))
            result = runtime.complete_switch(
                preparation,
                actor=actor,
                cancelled_operation_ids=[operation_id],
            )
        finally:
            runtime.abort_switch(preparation.token)

    assert result.current is PortalContour.TARGET
    assert result.cancelled_operation_ids == (operation_id,)
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.CANCELLED


def test_active_worker_stops_before_runtime_mode_changes(tmp_path: Path) -> None:
    settings, session_factory, manager, actor_id = _environment(tmp_path)

    async def scenario() -> int:
        await manager.startup()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def worker(context) -> None:  # type: ignore[no-untyped-def]
            context.transition(OperationStatus.VALIDATING)
            context.transition(OperationStatus.RUNNING)
            entered.set()
            await release.wait()

        handle = manager.create_and_submit(
            operation_type=OperationType.EXPORT,
            actor_user_id=actor_id,
            actor_username="admin",
            worker=worker,
        )
        await asyncio.wait_for(entered.wait(), timeout=5)

        with session_factory() as session:
            actor = session.get(User, actor_id)
            assert actor is not None
            runtime = RuntimeModeService(session, settings)
            preparation = runtime.begin_switch(PortalContour.TARGET)
            try:
                await manager.cancel(handle.operation_id)
                operation = manager.get_operation(handle.operation_id)
                assert operation is not None
                assert operation.status is OperationStatus.CANCELLED

                result = runtime.complete_switch(
                    preparation,
                    actor=actor,
                    cancelled_operation_ids=[handle.operation_id],
                )
            finally:
                runtime.abort_switch(preparation.token)

        await manager.shutdown()
        assert result.current is PortalContour.TARGET
        return handle.operation_id

    operation_id = asyncio.run(scenario())

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.status is OperationStatus.CANCELLED
        snapshot = RuntimeModeService(session, settings).current_snapshot()
        assert snapshot.mode is PortalContour.TARGET
