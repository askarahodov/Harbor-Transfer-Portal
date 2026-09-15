from __future__ import annotations

from collections.abc import Sequence

from app.config import PortalContour
from app.db.models import Operation
from app.domain.bundle import OperationStatus, OperationType
from app.services.operation_manager import (
    OperationArtifactSpec,
    OperationHandle,
    OperationManager,
    OperationManagerError,
    OperationWorker,
)
from app.services.runtime_mode import RuntimeModeError, RuntimeModeService
from app.utils.logging import operation_log_context


class CorrelatedOperationManager(OperationManager):
    """Operation manager with log correlation and runtime-mode serialization."""

    def create_operation(
        self,
        *,
        operation_type: OperationType,
        actor_user_id: int | None,
        actor_username: str,
        initial_status: OperationStatus | None = None,
        comment: str | None = None,
        delivery_id: str | None = None,
        artifacts: Sequence[OperationArtifactSpec] = (),
    ) -> int:
        required_mode = (
            PortalContour.SOURCE
            if operation_type is OperationType.EXPORT
            else PortalContour.TARGET
        )
        with self.session_factory() as runtime_session:
            runtime = RuntimeModeService(runtime_session, self.settings)
            with runtime.operation_start_guard(required_mode) as snapshot:
                operation_id = super().create_operation(
                    operation_type=operation_type,
                    actor_user_id=actor_user_id,
                    actor_username=actor_username,
                    initial_status=initial_status,
                    comment=comment,
                    delivery_id=delivery_id,
                    artifacts=artifacts,
                )
                # Keep the same process-wide runtime lock until the operation snapshot is
                # durable. A failure here is fail-closed: the already-created non-terminal
                # operation keeps mode switching blocked and can be diagnosed/reconciled.
                with self.session_factory() as snapshot_session:
                    operation = snapshot_session.get(Operation, operation_id)
                    if operation is None:
                        raise OperationManagerError(
                            "operation_not_found",
                            "Созданная операция не найдена для runtime snapshot",
                        )
                    operation.runtime_mode = snapshot.mode.value
                    operation.runtime_mode_version = snapshot.version
                    snapshot_session.commit()
                return operation_id

    def submit(self, operation_id: int, worker: OperationWorker) -> OperationHandle:
        operation = self.get_operation(operation_id)
        if operation is None:
            raise OperationManagerError("operation_not_found", "Операция не найдена")
        required_mode = (
            PortalContour.SOURCE
            if operation.type is OperationType.EXPORT
            else PortalContour.TARGET
        )
        with self.session_factory() as runtime_session:
            runtime = RuntimeModeService(runtime_session, self.settings)
            with runtime.operation_start_guard(required_mode) as snapshot:
                if operation.runtime_mode is None or operation.runtime_mode_version is None:
                    # Backfill a legacy non-terminal row when it is still compatible with
                    # the authoritative current mode. This avoids silently resuming it in
                    # another mode while preserving upgrade compatibility.
                    with self.session_factory() as snapshot_session:
                        persisted = snapshot_session.get(Operation, operation_id)
                        if persisted is None:
                            raise OperationManagerError(
                                "operation_not_found",
                                "Операция не найдена для runtime snapshot",
                            )
                        persisted.runtime_mode = snapshot.mode.value
                        persisted.runtime_mode_version = snapshot.version
                        snapshot_session.commit()
                elif (
                    operation.runtime_mode != snapshot.mode.value
                    or operation.runtime_mode_version != snapshot.version
                ):
                    raise RuntimeModeError(
                        "runtime_mode_operation_mismatch",
                        "Runtime mode операции не совпадает с текущим authoritative mode",
                    )
                return super().submit(operation_id, worker)

    async def _run_worker(
        self,
        operation_id: int,
        worker_token: str,
        worker: OperationWorker,
    ) -> None:
        # ContextVars are copied by asyncio.to_thread(), so binding before the
        # base worker starts also correlates logs emitted from its helper threads.
        with operation_log_context(operation_id):
            await super()._run_worker(operation_id, worker_token, worker)
