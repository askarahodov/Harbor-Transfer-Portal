from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import ArtifactResult, Operation
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.operations import TERMINAL_STATES, validate_transition
from app.services.harbor_profile_runtime import harbor_profile_boundary

logger = logging.getLogger(__name__)

_ACTIVE_EXECUTION_STATES = {
    OperationStatus.VALIDATING,
    OperationStatus.RUNNING,
    OperationStatus.PACKAGING,
    OperationStatus.VERIFYING,
    OperationStatus.IMPORTING,
    OperationStatus.VERIFYING_TARGET,
}

_ARTIFACT_TRANSITIONS: dict[ArtifactStatus, set[ArtifactStatus]] = {
    ArtifactStatus.PENDING: {
        ArtifactStatus.RUNNING,
        ArtifactStatus.SKIPPED,
        ArtifactStatus.CONFLICT,
        ArtifactStatus.FAILED,
    },
    ArtifactStatus.RUNNING: {
        ArtifactStatus.IMPORTED,
        ArtifactStatus.SKIPPED,
        ArtifactStatus.CONFLICT,
        ArtifactStatus.FAILED,
        ArtifactStatus.VERIFIED,
    },
    ArtifactStatus.IMPORTED: {
        ArtifactStatus.VERIFIED,
        ArtifactStatus.FAILED,
    },
}


@dataclass(slots=True)
class OperationManagerError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class OperationCancelled(Exception):
    pass


@dataclass(slots=True)
class OperationTaskFailure(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class OperationArtifactSpec:
    artifact_type: str
    repository: str
    name: str | None = None
    reference: str | None = None
    version: str | None = None
    source_digest: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class OperationHandle:
    operation_id: int


OperationWorker = Callable[["OperationContext"], Awaitable[None]]


class OperationContext:
    def __init__(
        self,
        manager: OperationManager,
        operation_id: int,
        worker_token: str,
    ) -> None:
        self.manager = manager
        self.operation_id = operation_id
        self.worker_token = worker_token

    def transition(self, target: OperationStatus) -> None:
        now = datetime.now(UTC)
        with self.manager.session_factory() as session:
            operation = self._owned_operation(session)
            validate_transition(operation.type, operation.status, target)
            operation.status = target
            operation.heartbeat_at = now
            if operation.started_at is None:
                operation.started_at = now
            if target in TERMINAL_STATES:
                operation.finished_at = now
            session.commit()

    def set_progress(self, *, current: int, total: int) -> None:
        if current < 0 or total < 0 or current > total:
            raise ValueError("operation progress must satisfy 0 <= current <= total")
        with self.manager.session_factory() as session:
            operation = self._owned_operation(session)
            operation.progress_current = current
            operation.progress_total = total
            operation.heartbeat_at = datetime.now(UTC)
            session.commit()

    def set_artifact_status(
        self,
        artifact_id: int,
        status: ArtifactStatus,
        *,
        target_digest: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self.manager.session_factory() as session:
            operation = self._owned_operation(session)
            artifact = session.scalar(
                select(ArtifactResult).where(
                    ArtifactResult.id == artifact_id,
                    ArtifactResult.operation_id == self.operation_id,
                )
            )
            if artifact is None:
                raise OperationManagerError(
                    "operation_artifact_not_found",
                    "Артефакт операции не найден",
                )
            if status not in _ARTIFACT_TRANSITIONS.get(artifact.status, set()):
                raise OperationManagerError(
                    "operation_artifact_transition_invalid",
                    f"Недопустимый переход статуса артефакта: {artifact.status} -> {status}",
                )
            if artifact.started_at is None:
                artifact.started_at = now
            if status is not ArtifactStatus.RUNNING:
                artifact.finished_at = now
            artifact.status = status
            if target_digest is not None:
                artifact.target_digest = target_digest
            artifact.error_code = error_code
            artifact.error_message = error_message
            if size_bytes is not None:
                artifact.size_bytes = size_bytes
            operation.heartbeat_at = now
            self.manager._recalculate_counts(session, operation)
            session.commit()

    def heartbeat(self) -> None:
        with self.manager.session_factory() as session:
            operation = self._owned_operation(session)
            operation.heartbeat_at = datetime.now(UTC)
            session.commit()

    def raise_if_cancelled(self) -> None:
        with self.manager.session_factory() as session:
            operation = self._owned_operation(session)
            if operation.cancel_requested_at is not None:
                raise OperationCancelled

    def workspace(self) -> Path:
        return self.manager.prepare_workspace(self.operation_id)

    def cleanup_workspace(self) -> None:
        self.manager.cleanup_workspace(self.operation_id)

    def require_disk(self, required_bytes: int = 0) -> None:
        self.manager.require_disk(required_bytes)

    def _owned_operation(self, session: Session) -> Operation:
        operation = session.get(Operation, self.operation_id)
        if operation is None:
            raise OperationManagerError("operation_not_found", "Операция не найдена")
        if operation.worker_token != self.worker_token:
            raise OperationManagerError(
                "operation_worker_claim_lost",
                "Worker больше не владеет операцией",
            )
        return operation


class OperationManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.workspace_root = settings.operation_workspace_root.resolve()
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._slots: asyncio.Semaphore | None = None
        self._shutting_down = False

    async def startup(self) -> None:
        self._shutting_down = False
        self._slots = asyncio.Semaphore(self.settings.operation_max_concurrent)
        self.reconcile_interrupted_operations()

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.settings.operation_shutdown_timeout_seconds,
                )
            except TimeoutError:
                logger.error("operation workers did not stop before shutdown timeout")
        self._tasks.clear()

    def create_operation(
        self,
        *,
        operation_type: OperationType,
        actor_user_id: int | None,
        actor_username: str,
        initial_status: OperationStatus | None = None,
        comment: str | None = None,
        delivery_id: str | None = None,
        harbor_profile_id: str | None = None,
        harbor_profile_name: str | None = None,
        harbor_url: str | None = None,
        artifacts: Sequence[OperationArtifactSpec] = (),
    ) -> int:
        status = initial_status or (
            OperationStatus.CREATED
            if operation_type is OperationType.EXPORT
            else OperationStatus.UPLOADED
        )
        self._validate_initial_status(operation_type, status)
        with harbor_profile_boundary():
            with self.session_factory() as session:
                operation = Operation(
                    delivery_id=delivery_id,
                    type=operation_type,
                    status=status,
                    actor_user_id=actor_user_id,
                    actor_username=actor_username,
                    comment=comment,
                    harbor_profile_id=harbor_profile_id,
                    harbor_profile_name=harbor_profile_name,
                    harbor_url=harbor_url,
                    progress_current=0,
                    progress_total=len(artifacts),
                    total_artifacts=len(artifacts),
                    successful_artifacts=0,
                    failed_artifacts=0,
                    skipped_artifacts=0,
                    conflict_artifacts=0,
                )
                session.add(operation)
                session.flush()
                for spec in artifacts:
                    session.add(
                        ArtifactResult(
                            operation_id=operation.id,
                            artifact_type=spec.artifact_type,
                            repository=spec.repository,
                            name=spec.name,
                            reference=spec.reference,
                            version=spec.version,
                            source_digest=spec.source_digest,
                            size_bytes=spec.size_bytes,
                            status=ArtifactStatus.PENDING,
                        )
                    )
                session.commit()
                return operation.id

    def create_and_submit(
        self,
        *,
        operation_type: OperationType,
        actor_user_id: int | None,
        actor_username: str,
        worker: OperationWorker,
        initial_status: OperationStatus | None = None,
        comment: str | None = None,
        delivery_id: str | None = None,
        harbor_profile_id: str | None = None,
        harbor_profile_name: str | None = None,
        harbor_url: str | None = None,
        artifacts: Sequence[OperationArtifactSpec] = (),
    ) -> OperationHandle:
        operation_id = self.create_operation(
            operation_type=operation_type,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            initial_status=initial_status,
            comment=comment,
            delivery_id=delivery_id,
            harbor_profile_id=harbor_profile_id,
            harbor_profile_name=harbor_profile_name,
            harbor_url=harbor_url,
            artifacts=artifacts,
        )
        self.submit(operation_id, worker)
        return OperationHandle(operation_id=operation_id)

    def submit(self, operation_id: int, worker: OperationWorker) -> OperationHandle:
        current = self._tasks.get(operation_id)
        if current is not None and not current.done():
            raise OperationManagerError(
                "operation_worker_already_running",
                "Операция уже выполняется этим экземпляром backend",
            )

        worker_token = secrets.token_hex(24)
        self._claim_worker(operation_id, worker_token)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self._run_worker(operation_id, worker_token, worker),
                name=f"operation-{operation_id}",
            )
        except RuntimeError as exc:
            self._release_worker(operation_id, worker_token)
            raise OperationManagerError(
                "operation_event_loop_required",
                "Фоновый worker должен запускаться из async event loop",
            ) from exc

        self._tasks[operation_id] = task
        task.add_done_callback(partial(self._forget_task, operation_id))
        return OperationHandle(operation_id=operation_id)

    async def wait(self, operation_id: int) -> None:
        task = self._tasks.get(operation_id)
        if task is not None:
            await asyncio.shield(task)

    async def cancel(self, operation_id: int) -> None:
        self._request_cancel(operation_id)
        task = self._tasks.get(operation_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            self._mark_cancelled(operation_id)
            self.cleanup_workspace(operation_id)

    def get_operation(self, operation_id: int) -> Operation | None:
        with self.session_factory() as session:
            statement = select(Operation).where(Operation.id == operation_id)
            operation = session.scalar(statement)
            if operation is None:
                return None
            _ = operation.artifacts
            session.expunge(operation)
            for artifact in operation.artifacts:
                session.expunge(artifact)
            return operation

    def reconcile_interrupted_operations(self) -> int:
        recovered = 0
        with self.session_factory() as session:
            statement = select(Operation).where(
                (Operation.worker_token.is_not(None))
                | (Operation.status.in_(tuple(_ACTIVE_EXECUTION_STATES)))
            )
            operations = list(session.scalars(statement))
            for operation in operations:
                if operation.status in TERMINAL_STATES:
                    operation.worker_token = None
                    operation.worker_started_at = None
                    operation.heartbeat_at = None
                    continue
                if operation.cancel_requested_at is not None:
                    validate_transition(
                        operation.type,
                        operation.status,
                        OperationStatus.CANCELLED,
                    )
                    operation.status = OperationStatus.CANCELLED
                    operation.finished_at = datetime.now(UTC)
                    operation.error_code = "operation_cancelled"
                    operation.error_message = "Операция была отменена"
                    self._fail_running_artifacts(
                        session,
                        operation,
                        "operation_cancelled",
                        "Выполнение артефакта прервано отменой операции",
                    )
                    self.cleanup_workspace(operation.id)
                    recovered += 1
                elif operation.status is not OperationStatus.READY:
                    validate_transition(
                        operation.type,
                        operation.status,
                        OperationStatus.FAILED,
                    )
                    operation.status = OperationStatus.FAILED
                    operation.finished_at = datetime.now(UTC)
                    operation.error_code = "operation_interrupted_restart"
                    operation.error_message = (
                        "Операция прервана перезапуском backend; автоматическое "
                        "возобновление в v1 не поддерживается"
                    )
                    self._fail_running_artifacts(
                        session,
                        operation,
                        "operation_interrupted_restart",
                        "Выполнение артефакта прервано перезапуском backend",
                    )
                    self.cleanup_workspace(operation.id)
                    recovered += 1
                operation.worker_token = None
                operation.worker_started_at = None
                operation.heartbeat_at = None
                self._recalculate_counts(session, operation)
            session.commit()
        return recovered

    def prepare_workspace(self, operation_id: int) -> Path:
        self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.workspace_root, 0o700)
        workspace = self.workspace_root / f"operation-{operation_id}"
        if workspace.is_symlink():
            raise OperationManagerError(
                "operation_workspace_unsafe",
                "Workspace операции не может быть symlink",
            )
        workspace.mkdir(parents=False, exist_ok=True, mode=0o700)
        os.chmod(workspace, 0o700)
        return workspace

    def cleanup_workspace(self, operation_id: int) -> None:
        workspace = self.workspace_root / f"operation-{operation_id}"
        if workspace.is_symlink():
            workspace.unlink(missing_ok=True)
        elif workspace.exists():
            shutil.rmtree(workspace)

    def require_disk(self, required_bytes: int = 0) -> None:
        if required_bytes < 0:
            raise ValueError("required_bytes must be non-negative")
        self.workspace_root.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(self.workspace_root.parent).free
        required = self.settings.operation_disk_reserve_bytes + required_bytes
        if free_bytes < required:
            raise OperationTaskFailure(
                "operation_insufficient_disk",
                "Недостаточно свободного места для запуска операции",
            )

    async def _run_worker(
        self,
        operation_id: int,
        worker_token: str,
        worker: OperationWorker,
    ) -> None:
        cleanup_workspace = False
        try:
            slots = self._slots
            if slots is None:
                slots = asyncio.Semaphore(self.settings.operation_max_concurrent)
                self._slots = slots
            async with slots:
                context = OperationContext(self, operation_id, worker_token)
                context.require_disk()
                context.raise_if_cancelled()
                await worker(context)
        except OperationCancelled:
            cleanup_workspace = True
            self._mark_cancelled(operation_id)
        except asyncio.CancelledError:
            if self._shutting_down:
                cleanup_workspace = self._handle_shutdown_interruption(operation_id)
            else:
                cleanup_workspace = True
                self._mark_cancelled(operation_id)
            raise
        except OperationTaskFailure as exc:
            cleanup_workspace = True
            self._mark_failed(operation_id, exc.code, exc.message)
        except Exception:
            cleanup_workspace = True
            logger.exception(
                "unexpected operation worker failure",
                extra={"operation_id": operation_id},
            )
            self._mark_failed(
                operation_id,
                "operation_worker_failed",
                "Фоновая операция завершилась с внутренней ошибкой",
            )
        finally:
            self._release_worker(operation_id, worker_token)
            if cleanup_workspace:
                self.cleanup_workspace(operation_id)

    def _claim_worker(self, operation_id: int, worker_token: str) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            statement = (
                update(Operation)
                .where(
                    Operation.id == operation_id,
                    Operation.worker_token.is_(None),
                    Operation.cancel_requested_at.is_(None),
                    Operation.status.not_in(tuple(TERMINAL_STATES)),
                )
                .values(
                    worker_token=worker_token,
                    worker_started_at=now,
                    heartbeat_at=now,
                )
            )
            result = cast(CursorResult[Any], session.execute(statement))
            session.commit()
            if result.rowcount == 1:
                return
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationManagerError("operation_not_found", "Операция не найдена")
            if operation.status in TERMINAL_STATES:
                raise OperationManagerError(
                    "operation_terminal",
                    "Терминальную операцию нельзя запустить повторно",
                )
            if operation.cancel_requested_at is not None:
                raise OperationManagerError(
                    "operation_cancel_requested",
                    "Для операции уже запрошена отмена",
                )
            raise OperationManagerError(
                "operation_worker_already_running",
                "Операция уже захвачена другим worker",
            )

    def _release_worker(self, operation_id: int, worker_token: str) -> None:
        with self.session_factory() as session:
            session.execute(
                update(Operation)
                .where(
                    Operation.id == operation_id,
                    Operation.worker_token == worker_token,
                )
                .values(
                    worker_token=None,
                    worker_started_at=None,
                    heartbeat_at=None,
                )
            )
            session.commit()

    def _request_cancel(self, operation_id: int) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationManagerError("operation_not_found", "Операция не найдена")
            if operation.status in TERMINAL_STATES:
                return
            operation.cancel_requested_at = operation.cancel_requested_at or datetime.now(UTC)
            session.commit()

    def _handle_shutdown_interruption(self, operation_id: int) -> bool:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.status in TERMINAL_STATES:
                return False
            if operation.status is OperationStatus.READY:
                return False
            validate_transition(operation.type, operation.status, OperationStatus.FAILED)
            operation.status = OperationStatus.FAILED
            operation.finished_at = now
            operation.error_code = "operation_interrupted_shutdown"
            operation.error_message = "Операция прервана остановкой backend"
            self._fail_running_artifacts(
                session,
                operation,
                "operation_interrupted_shutdown",
                "Выполнение артефакта прервано остановкой backend",
            )
            self._recalculate_counts(session, operation)
            session.commit()
        return True

    def _mark_cancelled(self, operation_id: int) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.status in TERMINAL_STATES:
                return
            validate_transition(operation.type, operation.status, OperationStatus.CANCELLED)
            operation.status = OperationStatus.CANCELLED
            operation.finished_at = now
            operation.error_code = "operation_cancelled"
            operation.error_message = "Операция отменена пользователем"
            self._fail_running_artifacts(
                session,
                operation,
                "operation_cancelled",
                "Выполнение артефакта прервано отменой операции",
            )
            self._recalculate_counts(session, operation)
            session.commit()

    def _mark_failed(self, operation_id: int, code: str, message: str) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.status in TERMINAL_STATES:
                return
            validate_transition(operation.type, operation.status, OperationStatus.FAILED)
            operation.status = OperationStatus.FAILED
            operation.finished_at = now
            operation.error_code = code
            operation.error_message = message
            self._fail_running_artifacts(session, operation, code, message)
            self._recalculate_counts(session, operation)
            session.commit()

    @staticmethod
    def _fail_running_artifacts(
        session: Session,
        operation: Operation,
        code: str,
        message: str,
    ) -> None:
        now = datetime.now(UTC)
        running = list(
            session.scalars(
                select(ArtifactResult).where(
                    ArtifactResult.operation_id == operation.id,
                    ArtifactResult.status == ArtifactStatus.RUNNING,
                )
            )
        )
        for artifact in running:
            artifact.status = ArtifactStatus.FAILED
            artifact.error_code = code
            artifact.error_message = message
            artifact.finished_at = now

    @staticmethod
    def _recalculate_counts(session: Session, operation: Operation) -> None:
        rows = session.execute(
            select(ArtifactResult.status, func.count(ArtifactResult.id))
            .where(ArtifactResult.operation_id == operation.id)
            .group_by(ArtifactResult.status)
        )
        counts = {status: count for status, count in rows}
        operation.successful_artifacts = counts.get(ArtifactStatus.IMPORTED, 0) + counts.get(
            ArtifactStatus.VERIFIED, 0
        )
        operation.failed_artifacts = counts.get(ArtifactStatus.FAILED, 0)
        operation.skipped_artifacts = counts.get(ArtifactStatus.SKIPPED, 0)
        operation.conflict_artifacts = counts.get(ArtifactStatus.CONFLICT, 0)

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
            raise ValueError(f"invalid initial status {status} for {operation_type}")

    def _forget_task(self, operation_id: int, completed: asyncio.Task[None]) -> None:
        current = self._tasks.get(operation_id)
        if current is completed:
            self._tasks.pop(operation_id, None)
