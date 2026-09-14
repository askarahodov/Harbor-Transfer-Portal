from __future__ import annotations

from app.services.operation_manager import OperationManager, OperationWorker
from app.utils.logging import operation_log_context


class CorrelatedOperationManager(OperationManager):
    """OperationManager that binds operation_id to the complete worker lifecycle."""

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
