import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db.base import Base
from app.db.models import ArtifactResult, Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.services.operation_manager import (
    OperationArtifactSpec,
    OperationContext,
    OperationManager,
    OperationManagerError,
)


def _manager(
    tmp_path: Path,
    *,
    max_concurrent: int = 2,
) -> OperationManager:
    database_url = f"sqlite:///{tmp_path / 'operations.db'}"
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


def _new_export(manager: OperationManager, *, artifacts: int = 0) -> int:
    specs = [
        OperationArtifactSpec(
            artifact_type="container-image",
            repository=f"project/app-{index}",
            reference="1.0.0",
        )
        for index in range(artifacts)
    ]
    return manager.create_operation(
        operation_type=OperationType.EXPORT,
        actor_user_id=None,
        actor_username="operator",
        artifacts=specs,
    )


def test_worker_persists_progress_and_artifact_results(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    operation_id = _new_export(manager, artifacts=2)
    artifacts = _operation(manager, operation_id).artifacts
    first_id, second_id = (artifact.id for artifact in artifacts)

    async def worker(context: OperationContext) -> None:
        context.transition(OperationStatus.VALIDATING)
        context.transition(OperationStatus.RUNNING)
        context.set_artifact_status(first_id, ArtifactStatus.RUNNING)
        context.set_artifact_status(
            first_id,
            ArtifactStatus.IMPORTED,
            target_digest="sha256:" + "a" * 64,
        )
        context.set_artifact_status(first_id, ArtifactStatus.VERIFIED)
        context.set_artifact_status(second_id, ArtifactStatus.SKIPPED)
        context.set_progress(current=2, total=2)
        context.transition(OperationStatus.PACKAGING)
        context.transition(OperationStatus.VERIFYING)
        context.transition(OperationStatus.COMPLETED)

    async def scenario() -> None:
        await manager.startup()
        manager.submit(operation_id, worker)
        await manager.wait(operation_id)
        await manager.shutdown()

    asyncio.run(scenario())

    operation = _operation(manager, operation_id)
    assert operation.status is OperationStatus.COMPLETED
    assert operation.progress_current == operation.progress_total == 2
    assert operation.successful_artifacts == 1
    assert operation.skipped_artifacts == 1
    assert operation.failed_artifacts == 0
    assert operation.conflict_artifacts == 0
    assert operation.worker_token is None
    assert [artifact.status for artifact in operation.artifacts] == [
        ArtifactStatus.VERIFIED,
        ArtifactStatus.SKIPPED,
    ]
    assert operation.artifacts[0].target_digest == "sha256:" + "a" * 64


def test_unexpected_worker_failure_is_safe_and_terminal(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    operation_id = _new_export(manager)

    async def worker(_context: OperationContext) -> None:
        raise RuntimeError("secret-token-must-not-be-persisted")

    async def scenario() -> None:
        await manager.startup()
        manager.submit(operation_id, worker)
        await manager.wait(operation_id)
        await manager.shutdown()

    asyncio.run(scenario())

    operation = _operation(manager, operation_id)
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "operation_worker_failed"
    assert operation.error_message == "Фоновая операция завершилась с внутренней ошибкой"
    assert "secret-token" not in operation.error_message


def test_cancellation_reaches_awaited_worker_and_is_persisted(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    operation_id = _new_export(manager)
    started = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def worker(context: OperationContext) -> None:
        context.transition(OperationStatus.VALIDATING)
        context.transition(OperationStatus.RUNNING)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            raise

    async def scenario() -> None:
        await manager.startup()
        manager.submit(operation_id, worker)
        await started.wait()
        await manager.cancel(operation_id)
        assert cancellation_seen.is_set()
        await manager.shutdown()

    asyncio.run(scenario())

    operation = _operation(manager, operation_id)
    assert operation.status is OperationStatus.CANCELLED
    assert operation.cancel_requested_at is not None
    assert operation.error_code == "operation_cancelled"


def test_persistent_claim_blocks_second_manager(tmp_path: Path) -> None:
    first = _manager(tmp_path)
    second = OperationManager(first.session_factory, first.settings)
    operation_id = _new_export(first)
    started = asyncio.Event()

    async def worker(context: OperationContext) -> None:
        context.transition(OperationStatus.VALIDATING)
        started.set()
        await asyncio.Event().wait()

    async def scenario() -> None:
        await first.startup()
        await second.startup()
        first.submit(operation_id, worker)
        await started.wait()
        with pytest.raises(OperationManagerError) as exc:
            second.submit(operation_id, worker)
        assert exc.value.code == "operation_worker_already_running"
        await first.cancel(operation_id)
        await first.shutdown()
        await second.shutdown()

    asyncio.run(scenario())


def test_concurrency_limit_queues_second_worker(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_concurrent=1)
    first_id = _new_export(manager)
    second_id = _new_export(manager)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_worker(context: OperationContext) -> None:
        context.transition(OperationStatus.VALIDATING)
        first_started.set()
        await release_first.wait()
        context.transition(OperationStatus.RUNNING)
        context.transition(OperationStatus.PACKAGING)
        context.transition(OperationStatus.VERIFYING)
        context.transition(OperationStatus.COMPLETED)

    async def second_worker(context: OperationContext) -> None:
        context.transition(OperationStatus.VALIDATING)
        second_started.set()
        context.transition(OperationStatus.RUNNING)
        context.transition(OperationStatus.PACKAGING)
        context.transition(OperationStatus.VERIFYING)
        context.transition(OperationStatus.COMPLETED)

    async def scenario() -> None:
        await manager.startup()
        manager.submit(first_id, first_worker)
        manager.submit(second_id, second_worker)
        await first_started.wait()
        await asyncio.sleep(0.05)
        assert not second_started.is_set()
        release_first.set()
        await manager.wait(first_id)
        await manager.wait(second_id)
        assert second_started.is_set()
        await manager.shutdown()

    asyncio.run(scenario())

    assert _operation(manager, first_id).status is OperationStatus.COMPLETED
    assert _operation(manager, second_id).status is OperationStatus.COMPLETED


def test_restart_reconciliation_fails_active_operation_and_cleans_workspace(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    operation_id = _new_export(manager, artifacts=1)
    workspace = manager.prepare_workspace(operation_id)
    (workspace / "partial").write_text("partial", encoding="utf-8")

    with manager.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.status = OperationStatus.RUNNING
        operation.worker_token = "stale-worker"
        operation.worker_started_at = datetime.now(UTC)
        operation.heartbeat_at = datetime.now(UTC)
        artifact = session.scalar(
            select(ArtifactResult).where(ArtifactResult.operation_id == operation_id)
        )
        assert artifact is not None
        artifact.status = ArtifactStatus.RUNNING
        artifact.started_at = datetime.now(UTC)
        session.commit()

    recovered = manager.reconcile_interrupted_operations()
    assert recovered == 1

    operation = _operation(manager, operation_id)
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "operation_interrupted_restart"
    assert operation.worker_token is None
    assert operation.artifacts[0].status is ArtifactStatus.FAILED
    assert not workspace.exists()


def test_restart_releases_waiting_ready_claim_without_destroying_workspace(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    operation_id = manager.create_operation(
        operation_type=OperationType.IMPORT,
        actor_user_id=None,
        actor_username="operator",
        initial_status=OperationStatus.UPLOADED,
    )
    workspace = manager.prepare_workspace(operation_id)
    (workspace / "verified-marker").write_text("ok", encoding="utf-8")

    with manager.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.status = OperationStatus.READY
        operation.worker_token = "stale-ready-worker"
        operation.worker_started_at = datetime.now(UTC)
        session.commit()

    recovered = manager.reconcile_interrupted_operations()
    assert recovered == 0

    operation = _operation(manager, operation_id)
    assert operation.status is OperationStatus.READY
    assert operation.worker_token is None
    assert workspace.is_dir()
