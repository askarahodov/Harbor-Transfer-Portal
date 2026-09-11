from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import ArtifactResult, Operation, User
from app.db.repositories import OperationRepository
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.operations import TERMINAL_STATES

_SUCCESS_ARTIFACT_STATES = {ArtifactStatus.IMPORTED, ArtifactStatus.VERIFIED}
_FINISHED_ARTIFACT_STATES = {
    ArtifactStatus.IMPORTED,
    ArtifactStatus.SKIPPED,
    ArtifactStatus.CONFLICT,
    ArtifactStatus.FAILED,
    ArtifactStatus.VERIFIED,
}
_RESTART_INTERRUPTED_STATES = {
    OperationStatus.CREATED,
    OperationStatus.VALIDATING,
    OperationStatus.RUNNING,
    OperationStatus.PACKAGING,
    OperationStatus.VERIFYING,
    OperationStatus.IMPORTING,
    OperationStatus.VERIFYING_TARGET,
}


@dataclass(slots=True)
class OperationManagerError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class OperationArtifactSpec:
    artifact_type: str
    repository: str
    reference: str | None = None
    version: str | None = None
    name: str | None = None
    source_digest: str | None = None


@dataclass(frozen=True, slots=True)
class OperationProgressSnapshot:
    operation_id: int
    status: OperationStatus
    progress_current: int
    progress_total: int
    total_artifacts: int
    completed_artifacts: int
    running_artifacts: int
    successful_artifacts: int
    failed_artifacts: int
    skipped_artifacts: int
    conflict_artifacts: int
    current_artifact_id: int | None


OperationWorker = Callable[["OperationExecutionContext"], Awaitable[None]]


class OperationExecutionContext:
    def __init__(
        self,
        manager: "OperationManager",
        operation_id: int,
        workspace: Path,
    ) -> None:
        self._manager = manager
        self.operation_id = operation_id
        self.workspace = workspace

    def transition(self, status: OperationStatus) -> OperationProgressSnapshot:
        now = datetime.now(UTC)
        with self._manager.session_factory() as session:
            repository = OperationRepository(session)
            operation = repository.get(self.operation_id)
            if operation is None:
                raise OperationManagerError(
                    "operation_not_found",
                    "Операция не найдена",
                )
            repository.transition(operation, status)
            if operation.started_at is None and status not in {
                OperationStatus.CREATED,
                OperationStatus.UPLOADED,
                OperationStatus.DISCOVERED,
                OperationStatus.READY,
            }:
                operation.started_at = now
            if status in TERMINAL_STATES:
                operation.finished_at = now
            session.commit()
        return self._manager.get_snapshot(self.operation_id)

    def set_progress(self, current: int, total: int) -> OperationProgressSnapshot:
        if current < 0 or total < 0 or current > total:
            raise OperationManagerError(
                "operation_progress_invalid",
                "Прогресс операции должен удовлетворять "
                "0 <= current <= total",
            )
        with self._manager.session_factory() as session:
            operation = OperationRepository(session).get(self.operation_id)
            if operation is None:
                raise OperationManagerError(
                    "operation_not_found",
                    "Операция не найдена",
                )
            operation.progress_current = current
            operation.progress_total = total
            session.commit()
        return self._manager.get_snapshot(self.operation_id)

    def set_delivery_id(self, delivery_id: str) -> None:
        with self._manager.session_factory() as session:
            operation = OperationRepository(session).get(self.operation_id)
            if operation is None:
                raise OperationManagerError(
                    "operation_not_found",
                    "Операция не найдена",
                )
            operation.delivery_id = delivery_id
            session.commit()

    def update_artifact(
        self,
        artifact_id: int,
        status: ArtifactStatus,
        *,
        target_digest: str | None = None,
        size_bytes: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> OperationProgressSnapshot:
        now = datetime.now(UTC)
        with self._manager.session_factory() as session:
            operation = OperationRepository(session).get(self.operation_id)
            if operation is None:
                raise OperationManagerError(
                    "operation_not_found",
                    "Операция не найдена",
                )
            artifact = next((item for item in operation.artifacts if item.id == artifact_id), None)
            if artifact is None:
                raise OperationManagerError(
                    "operation_artifact_not_found",
                    "Артефакт операции не найден",
                )
            if status is ArtifactStatus.RUNNING and artifact.started_at is None:
                artifact.started_at = now
            if status in _FINISHED_ARTIFACT_STATES:
                artifact.finished_at = now
            artifact.status = status
            if target_digest is not None:
                artifact.target_digest = target_digest
            if size_bytes is not None:
                if size_bytes < 0:
                    raise OperationManagerError(
                        "operation_artifact_size_invalid",
                        "Размер артефакта не может быть "
                        "отрицательным",
                    )
                artifact.size_bytes = size_bytes
            artifact.error_code = error_code
            artifact.error_message = error_message
            self._recount_artifacts(operation)
            session.commit()
        return self._manager.get_snapshot(self.operation_id)

    @staticmethod
    def _recount_artifacts(operation: Operation) -> None:
        operation.total_artifacts = len(operation.artifacts)
        operation.successful_artifacts = sum(
            artifact.status in _SUCCESS_ARTIFACT_STATES for artifact in operation.artifacts
        )
        operation.failed_artifacts = sum(
            artifact.status is ArtifactStatus.FAILED for artifact in operation.artifacts
        )
        operation.skipped_artifacts = sum(
            artifact.status is ArtifactStatus.SKIPPED for artifact in operation.artifacts
        )


class OperationManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.workspace_root = settings.operation_workspace_root.resolve()
        self._semaphore = asyncio.Semaphore(settings.operation_max_concurrent_operations)
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._cancel_reasons: dict[int, tuple[str, str]] = {}
        self._guard = asyncio.Lock()
        self._shutting_down = False

    def startup(self) -> int:
        self._shutting_down = False
        self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.workspace_root, 0o700)
        recovered: list[int] = []
        with self.session_factory() as session:
            operations = list(
                session.scalars(
                    select(Operation).where(Operation.status.in_(_RESTART_INTERRUPTED_STATES))
                )
            )
            now = datetime.now(UTC)
            for operation in operations:
                operation.status = OperationStatus.FAILED
                operation.error_code = "operation_interrupted_restart"
                operation.error_message = (
                    "Операция прервана перезапуском backend; "
                    "автоматическое возобновление v1 "
                    "не поддерживается"
                )
                operation.finished_at = now
                recovered.append(operation.id)
            session.commit()
        for operation_id in recovered:
            shutil.rmtree(self._workspace_path(operation_id), ignore_errors=True)
        return len(recovered)

    async def shutdown(self) -> None:
        self._shutting_down = True
        async with self._guard:
            tasks = list(self._tasks.items())
            for operation_id, task in tasks:
                if not task.done():
                    self._cancel_reasons[operation_id] = (
                        "operation_cancelled_shutdown",
                        "Операция отменена при штатной "
                        "остановке backend",
                    )
                    task.cancel()
        if tasks:
            await asyncio.gather(*(task for _operation_id, task in tasks), return_exceptions=True)

    def create_operation(
        self,
        *,
        operation_type: OperationType,
        initial_status: OperationStatus,
        actor: User | None,
        actor_username: str,
        artifacts: Sequence[OperationArtifactSpec] = (),
        comment: str | None = None,
    ) -> int:
        self._validate_initial_status(operation_type, initial_status)
        with self.session_factory() as session:
            repository = OperationRepository(session)
            operation = repository.create(
                operation_type=operation_type,
                status=initial_status,
                actor=actor,
                actor_username=actor_username,
                comment=comment,
            )
            for artifact in artifacts:
                repository.add_artifact(
                    operation,
                    artifact_type=artifact.artifact_type,
                    repository=artifact.repository,
                    reference=artifact.reference,
                    version=artifact.version,
                    name=artifact.name,
                    source_digest=artifact.source_digest,
                )
            operation.total_artifacts = len(artifacts)
            operation.progress_total = len(artifacts)
            session.commit()
            return operation.id

    async def create_and_start(
        self,
        *,
        operation_type: OperationType,
        initial_status: OperationStatus,
        actor: User | None,
        actor_username: str,
        worker: OperationWorker,
        artifacts: Sequence[OperationArtifactSpec] = (),
        comment: str | None = None,
        required_free_bytes: int = 0,
    ) -> int:
        operation_id = self.create_operation(
            operation_type=operation_type,
            initial_status=initial_status,
            actor=actor,
            actor_username=actor_username,
            artifacts=artifacts,
            comment=comment,
        )
        try:
            await self.start(operation_id, worker, required_free_bytes=required_free_bytes)
        except Exception:
            self._mark_failed(
                operation_id,
                code="operation_start_failed",
                message="Не удалось запустить фоновую операцию",
            )
            raise
        return operation_id

    async def start(
        self,
        operation_id: int,
        worker: OperationWorker,
        *,
        required_free_bytes: int = 0,
    ) -> None:
        if required_free_bytes < 0:
            raise OperationManagerError(
                "operation_disk_requirement_invalid",
                "Требуемый свободный объём не может быть "
                "отрицательным",
            )
        async with self._guard:
            if self._shutting_down:
                raise OperationManagerError(
                    "operation_manager_stopping",
                    "Менеджер операций останавливается",
                )
            existing = self._tasks.get(operation_id)
            if existing is not None and not existing.done():
                raise OperationManagerError(
                    "operation_already_running",
                    "Для операции уже запущен worker",
                )
            self._require_startable(operation_id)
            task = asyncio.create_task(
                self._run_operation(operation_id, worker, required_free_bytes),
                name=f"operation-{operation_id}",
            )
            self._tasks[operation_id] = task
            task.add_done_callback(
                lambda completed, oid=operation_id: self._drop_task(oid, completed)
            )

    async def wait(self, operation_id: int) -> OperationProgressSnapshot:
        async with self._guard:
            task = self._tasks.get(operation_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.get_snapshot(operation_id)

    async def cancel(self, operation_id: int) -> OperationProgressSnapshot:
        snapshot = self.get_snapshot(operation_id)
        if snapshot.status in TERMINAL_STATES:
            raise OperationManagerError(
                "operation_not_cancellable",
                "Операция уже находится в терминальном "
                "состоянии",
            )
        async with self._guard:
            task = self._tasks.get(operation_id)
            if task is not None and not task.done():
                self._cancel_reasons[operation_id] = (
                    "operation_cancelled_by_user",
                    "Операция отменена пользователем",
                )
                task.cancel()
            else:
                task = None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        else:
            self._mark_cancelled(
                operation_id,
                code="operation_cancelled_by_user",
                message="Операция отменена пользователем",
            )
        return self.get_snapshot(operation_id)

    def get_snapshot(self, operation_id: int) -> OperationProgressSnapshot:
        with self.session_factory() as session:
            operation = OperationRepository(session).get(operation_id)
            if operation is None:
                raise OperationManagerError(
                    "operation_not_found",
                    "Операция не найдена",
                )
            statuses = [artifact.status for artifact in operation.artifacts]
            completed = sum(status in _FINISHED_ARTIFACT_STATES for status in statuses)
            running = sum(status is ArtifactStatus.RUNNING for status in statuses)
            conflicts = sum(status is ArtifactStatus.CONFLICT for status in statuses)
            current = next(
                (
                    artifact.id
                    for artifact in operation.artifacts
                    if artifact.status is ArtifactStatus.RUNNING
                ),
                None,
            )
            return OperationProgressSnapshot(
                operation_id=operation.id,
                status=operation.status,
                progress_current=operation.progress_current,
                progress_total=operation.progress_total,
                total_artifacts=len(operation.artifacts),
                completed_artifacts=completed,
                running_artifacts=running,
                successful_artifacts=operation.successful_artifacts,
                failed_artifacts=operation.failed_artifacts,
                skipped_artifacts=operation.skipped_artifacts,
                conflict_artifacts=conflicts,
                current_artifact_id=current,
            )

    async def _run_operation(
        self,
        operation_id: int,
        worker: OperationWorker,
        required_free_bytes: int,
    ) -> None:
        workspace = self._workspace_path(operation_id)
        try:
            async with self._semaphore:
                self._prepare_workspace(workspace, required_free_bytes)
                context = OperationExecutionContext(self, operation_id, workspace)
                await worker(context)
                snapshot = self.get_snapshot(operation_id)
                if snapshot.status not in TERMINAL_STATES:
                    self._mark_failed(
                        operation_id,
                        code="operation_worker_incomplete",
                        message=(
                            "Worker завершился без терминального "
                            "статуса операции"
                        ),
                    )
        except asyncio.CancelledError:
            code, message = self._cancel_reasons.pop(
                operation_id,
                ("operation_cancelled", "Операция отменена"),
            )
            self._mark_cancelled(operation_id, code=code, message=message)
            raise
        except OperationManagerError as exc:
            self._mark_failed(operation_id, code=exc.code, message=exc.message)
        except Exception:
            self._mark_failed(
                operation_id,
                code="operation_worker_failed",
                message=(
                    "Фоновая операция завершилась внутренней "
                    "ошибкой"
                ),
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _prepare_workspace(self, workspace: Path, required_free_bytes: int) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        free = shutil.disk_usage(self.workspace_root).free
        required = max(required_free_bytes, self.settings.operation_min_free_bytes)
        if free < required:
            raise OperationManagerError(
                "operation_insufficient_disk",
                "Недостаточно свободного места для запуска "
                "операции",
            )
        if workspace.exists():
            raise OperationManagerError(
                "operation_workspace_exists",
                "Изолированный workspace операции уже существует",
            )
        workspace.mkdir(mode=0o700)

    def _require_startable(self, operation_id: int) -> None:
        with self.session_factory() as session:
            operation = OperationRepository(session).get(operation_id)
            if operation is None:
                raise OperationManagerError(
                    "operation_not_found",
                    "Операция не найдена",
                )
            if operation.status in TERMINAL_STATES:
                raise OperationManagerError(
                    "operation_not_startable",
                    "Терминальную операцию нельзя запустить "
                    "повторно",
                )

    def _mark_cancelled(self, operation_id: int, *, code: str, message: str) -> None:
        with self.session_factory() as session:
            repository = OperationRepository(session)
            operation = repository.get(operation_id)
            if operation is None or operation.status in TERMINAL_STATES:
                return
            try:
                repository.transition(operation, OperationStatus.CANCELLED)
            except ValueError:
                operation.status = OperationStatus.CANCELLED
            operation.error_code = code
            operation.error_message = message
            operation.finished_at = datetime.now(UTC)
            session.commit()

    def _mark_failed(self, operation_id: int, *, code: str, message: str) -> None:
        with self.session_factory() as session:
            repository = OperationRepository(session)
            operation = repository.get(operation_id)
            if operation is None or operation.status in TERMINAL_STATES:
                return
            try:
                repository.transition(operation, OperationStatus.FAILED)
            except ValueError:
                operation.status = OperationStatus.FAILED
            operation.error_code = code
            operation.error_message = message
            operation.finished_at = datetime.now(UTC)
            session.commit()

    def _workspace_path(self, operation_id: int) -> Path:
        return self.workspace_root / f"operation-{operation_id}"

    def _drop_task(self, operation_id: int, completed: asyncio.Task[None]) -> None:
        if self._tasks.get(operation_id) is completed:
            self._tasks.pop(operation_id, None)
        self._cancel_reasons.pop(operation_id, None)

    @staticmethod
    def _validate_initial_status(
        operation_type: OperationType,
        status: OperationStatus,
    ) -> None:
        allowed = (
            {OperationStatus.CREATED}
            if operation_type is OperationType.EXPORT
            else {OperationStatus.UPLOADED, OperationStatus.DISCOVERED}
        )
        if status not in allowed:
            raise OperationManagerError(
                "operation_initial_status_invalid",
                "Начальный статус не соответствует типу "
                "операции",
            )
