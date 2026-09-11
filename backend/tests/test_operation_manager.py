import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.services.operation_manager import (
    OperationArtifactSpec,
    OperationManager,
    OperationManagerError,
)


def _manager(
    tmp_path: Path,
    *,
    max_concurrent: int = 2,
    min_free_bytes: int = 0,
) -> OperationManager:
    database_url = f"sqlite:///{tmp_path / 'operations.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        operation_workspace_root=tmp_path / "workspaces",
        operation_max_concurrent_operations=max_concurrent,
        operation_min_free_bytes=min_free_bytes,
    )
    return OperationManager(create_session_factory(engine), settings)


def test_progress_and_artifact_results_are_persistent(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager.startup()
        operation_id = manager.create_operation(
            operation_type=OperationType.EXPORT,
            initial_status=OperationStatus.CREATED,
            actor=None,
            actor_username="operator",
            artifacts=[
                OperationArtifactSpec("container-image", "project/app", reference="1.0.0"),
                OperationArtifactSpec(
                    "helm-chart",
                    "project/charts",
                    name="chart",
                    version="1.2.3",
                ),
            ],
        )

        async def worker(context) -> None:  # type: ignore[no-untyped-def]
            context.transition(OperationStatus.VALIDATING)
            context.transition(OperationStatus.RUNNING)
            context.set_progress(0, 2)
            artifact_ids = []
            with manager.session_factory() as session:
                operation = session.get(Operation, operation_id)
                assert operation is not None
                artifact_ids = [artifact.id for artifact in operation.artifacts]
            context.update_artifact(artifact_ids[0], ArtifactStatus.RUNNING)
            context.update_artifact(artifact_ids[0], ArtifactStatus.VERIFIED)
            context.set_progress(1, 2)
            context.update_artifact(artifact_ids[1], ArtifactStatus.RUNNING)
            context.update_artifact(artifact_ids[1], ArtifactStatus.SKIPPED)
            context.set_progress(2, 2)
            context.transition(OperationStatus.PACKAGING)
            context.transition(OperationStatus.VERIFYING)
            context.transition(OperationStatus.COMPLETED)

        await manager.start(operation_id, worker)
        result = await manager.wait(operation_id)
        assert result.status is OperationStatus.COMPLETED
        assert result.progress_current == result.progress_total == 2
        assert result.completed_artifacts == 2
        assert result.successful_artifacts == 1
        assert result.skipped_artifacts == 1
        assert result.current_artifact_id is None
        await manager.shutdown()

    asyncio.run(scenario())


def test_duplicate_worker_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager.startup()
        started = asyncio.Event()
        release = asyncio.Event()
        operation_id = manager.create_operation(
            operation_type=OperationType.EXPORT,
            initial_status=OperationStatus.CREATED,
            actor=None,
            actor_username="operator",
        )

        async def worker(context) -> None:  # type: ignore[no-untyped-def]
            context.transition(OperationStatus.VALIDATING)
            started.set()
            await release.wait()
            context.transition(OperationStatus.RUNNING)
            context.transition(OperationStatus.PACKAGING)
            context.transition(OperationStatus.VERIFYING)
            context.transition(OperationStatus.COMPLETED)

        await manager.start(operation_id, worker)
        await started.wait()
        with pytest.raises(OperationManagerError) as exc:
            await manager.start(operation_id, worker)
        assert exc.value.code == "operation_already_running"
        release.set()
        assert (await manager.wait(operation_id)).status is OperationStatus.COMPLETED
        await manager.shutdown()

    asyncio.run(scenario())


def test_concurrency_limit_serializes_workers_and_workspaces_are_isolated(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path, max_concurrent=1)
        manager.startup()
        active = 0
        max_active = 0
        workspaces: list[Path] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        first = manager.create_operation(
            operation_type=OperationType.EXPORT,
            initial_status=OperationStatus.CREATED,
            actor=None,
            actor_username="one",
        )
        second = manager.create_operation(
            operation_type=OperationType.EXPORT,
            initial_status=OperationStatus.CREATED,
            actor=None,
            actor_username="two",
        )

        async def worker(context) -> None:  # type: ignore[no-untyped-def]
            nonlocal active, max_active
            context.transition(OperationStatus.VALIDATING)
            active += 1
            max_active = max(max_active, active)
            workspaces.append(context.workspace)
            if context.operation_id == first:
                first_started.set()
                await release_first.wait()
            await asyncio.sleep(0)
            active -= 1
            context.transition(OperationStatus.RUNNING)
            context.transition(OperationStatus.PACKAGING)
            context.transition(OperationStatus.VERIFYING)
            context.transition(OperationStatus.COMPLETED)

        await manager.start(first, worker)
        await manager.start(second, worker)
        await first_started.wait()
        await asyncio.sleep(0.02)
        assert len(workspaces) == 1
        release_first.set()
        await manager.wait(first)
        await manager.wait(second)
        assert max_active == 1
        assert len(workspaces) == 2
        assert workspaces[0] != workspaces[1]
        assert all(not workspace.exists() for workspace in workspaces)
        await manager.shutdown()

    asyncio.run(scenario())


def test_cancel_propagates_cancelled_error_to_worker_service(tmp_path: Path) -> None:
    class FakeSkopeoService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        async def export_image(self) -> None:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager.startup()
        fake = FakeSkopeoService()
        operation_id = manager.create_operation(
            operation_type=OperationType.EXPORT,
            initial_status=OperationStatus.CREATED,
            actor=None,
            actor_username="operator",
        )

        async def worker(context) -> None:  # type: ignore[no-untyped-def]
            context.transition(OperationStatus.VALIDATING)
            context.transition(OperationStatus.RUNNING)
            await fake.export_image()

        await manager.start(operation_id, worker)
        await fake.started.wait()
        result = await manager.cancel(operation_id)
        assert fake.cancelled is True
        assert result.status is OperationStatus.CANCELLED
        with manager.session_factory() as session:
            operation = session.get(Operation, operation_id)
            assert operation is not None
            assert operation.error_code == "operation_cancelled_by_user"
        await manager.shutdown()

    asyncio.run(scenario())


def test_restart_reconciliation_fails_only_nonresumable_active_work(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.startup()
    export_id = manager.create_operation(
        operation_type=OperationType.EXPORT,
        initial_status=OperationStatus.CREATED,
        actor=None,
        actor_username="operator",
    )
    import_id = manager.create_operation(
        operation_type=OperationType.IMPORT,
        initial_status=OperationStatus.UPLOADED,
        actor=None,
        actor_username="operator",
    )
    with manager.session_factory() as session:
        from app.db.repositories import OperationRepository

        repository = OperationRepository(session)
        export_operation = repository.get(export_id)
        import_operation = repository.get(import_id)
        assert export_operation is not None and import_operation is not None
        repository.transition(export_operation, OperationStatus.VALIDATING)
        repository.transition(export_operation, OperationStatus.RUNNING)
        repository.transition(import_operation, OperationStatus.VERIFYING)
        repository.transition(import_operation, OperationStatus.READY)
        session.commit()
    stale_workspace = manager.workspace_root / f"operation-{export_id}"
    stale_workspace.mkdir(parents=True)
    (stale_workspace / "partial").write_text("partial", encoding="utf-8")

    restarted = OperationManager(manager.session_factory, manager.settings)
    assert restarted.startup() == 1
    with restarted.session_factory() as session:
        export_operation = session.get(Operation, export_id)
        import_operation = session.get(Operation, import_id)
        assert export_operation is not None and import_operation is not None
        assert export_operation.status is OperationStatus.FAILED
        assert export_operation.error_code == "operation_interrupted_restart"
        assert import_operation.status is OperationStatus.READY
    assert not stale_workspace.exists()


def test_worker_exception_is_redacted_to_stable_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager.startup()
        operation_id = manager.create_operation(
            operation_type=OperationType.EXPORT,
            initial_status=OperationStatus.CREATED,
            actor=None,
            actor_username="operator",
        )

        async def worker(context) -> None:  # type: ignore[no-untyped-def]
            context.transition(OperationStatus.VALIDATING)
            raise RuntimeError("HARBOR_PASSWORD=super-secret")

        await manager.start(operation_id, worker)
        result = await manager.wait(operation_id)
        assert result.status is OperationStatus.FAILED
        with manager.session_factory() as session:
            operation = session.get(Operation, operation_id)
            assert operation is not None
            assert operation.error_code == "operation_worker_failed"
            assert "super-secret" not in (operation.error_message or "")
        await manager.shutdown()

    asyncio.run(scenario())
