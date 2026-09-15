from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult

from app.db.models import Operation
from app.domain.bundle import OperationStatus, OperationType
from app.schemas.import_retries import retry_lineage_from_policy
from app.schemas.imports import (
    ImportDestinationPlanRequest,
    ImportDestinationPlanResponse,
    ImportPreviewResponse,
    ImportReceiptArtifactResponse,
    ImportReceiptResponse,
)
from app.services.artifact_mapping_snapshot import persist_artifact_mapping_snapshot
from app.services.destination_mapping_policy import DestinationMappingPolicyService
from app.services.import_destination_plan import (
    _POLICY_VERSION,
    ImportDestinationPlanOrchestrator,
)
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.operation_manager import OperationContext, OperationTaskFailure


class PolicyAwareImportDestinationPlanOrchestrator(ImportDestinationPlanOrchestrator):
    """Resolve admin defaults once per new plan and freeze the resulting policy revision."""

    async def build_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
        *,
        actor_username: str | None = None,
    ) -> ImportDestinationPlanResponse:
        operation = self.operation_manager.get_operation(operation_id)
        if operation is not None and operation.worker_token is not None:
            raise ImportOrchestrationError(
                "import_destination_plan_stale",
                "Import worker уже захватил operation; destination plan больше нельзя менять",
            )
        if operation is not None and getattr(operation, "import_policy_json", None) is not None:
            policy = self._policy_object(operation)
            if "retry" in policy:
                raise ImportOrchestrationError(
                    "import_retry_mapping_immutable",
                    "Retry operation привязана к исходному destination plan; "
                    "для другого mapping создайте новый Preview/import",
                )
        with self.session_factory() as session:
            effective_mapping = DestinationMappingPolicyService(session).resolve_request(mapping)
        return await super().build_destination_plan(
            operation_id,
            effective_mapping,
            actor_username=actor_username,
        )

    async def _import_worker(self, context: OperationContext, operation_id: int) -> None:
        plan = self.destination_plan(operation_id)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.type is not OperationType.IMPORT:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Import operation отсутствует перед сохранением destination snapshot",
                )
            policy = self._policy_object(operation)
            persist_artifact_mapping_snapshot(
                session,
                operation,
                plan,
                overwrite_approved=bool(policy.get("overwrite_conflicts", False)),
            )
            # Commit the immutable reporting/audit snapshot before the existing worker
            # performs mapped TARGET preflight or any Harbor mutation.
            session.commit()
        await super()._import_worker(context, operation_id)

    def _persist_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
        plan: ImportDestinationPlanResponse,
    ) -> None:
        # canonical_plan_hash already includes mapping.mapping_policy_revision. Stamp the
        # same server-owned snapshot onto the response before the immutable plan is stored.
        plan.mapping_policy_revision = mapping.mapping_policy_revision
        payload = self._dump_policy(
            {
                "version": _POLICY_VERSION,
                "mapping_request": mapping.model_dump(mode="json"),
                "destination_plan": plan.model_dump(mode="json"),
            }
        )
        with self.session_factory() as session:
            statement = (
                update(Operation)
                .where(
                    Operation.id == operation_id,
                    Operation.type == OperationType.IMPORT,
                    Operation.status == OperationStatus.READY,
                    Operation.worker_token.is_(None),
                    Operation.bundle_sha256 == plan.bundle_sha256,
                    Operation.source_delivery_id == plan.source_delivery_id,
                )
                .values(import_policy_json=payload)
            )
            result = cast(CursorResult[Any], session.execute(statement))
            session.commit()
            if result.rowcount == 1:
                return

            operation = session.get(Operation, operation_id)
            if operation is None or operation.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Import-операция не найдена",
                )
            raise ImportOrchestrationError(
                "import_destination_plan_stale",
                "Import execution уже начался либо bundle/delivery изменились; plan не сохранён",
            )

    def _write_receipt(
        self,
        operation_id: int,
        preview: ImportPreviewResponse,
        overwrite: bool,
        requested_at: datetime,
        failures: int,
    ) -> None:
        plan = self.destination_plan(operation_id)
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Operation отсутствует при формировании receipt",
                )
            rows = sorted(operation.artifacts, key=lambda item: item.id)
            if len(rows) != len(plan.artifacts):
                raise OperationTaskFailure(
                    "import_destination_plan_tampered",
                    "Destination plan не совпадает с persisted artifact rows",
                )
            lineage = retry_lineage_from_policy(operation.import_policy_json)
            receipt = ImportReceiptResponse(
                operation_id=operation.id,
                source_delivery_id=preview.source_delivery_id,
                bundle_sha256=preview.bundle_sha256,
                actor_username=operation.actor_username,
                started_at=requested_at,
                finished_at=now,
                overwrite_conflicts=overwrite,
                destination_plan_id=plan.plan_id,
                destination_plan_hash=plan.plan_hash,
                retry_of_operation_id=(lineage.retry_of_operation_id if lineage else None),
                failure_policy=(lineage.failure_policy if lineage else None),
                result="FAILED" if failures else "COMPLETED",
                artifacts=[
                    ImportReceiptArtifactResponse(
                        index=index,
                        artifact_type=row.artifact_type,
                        repository=row.repository,
                        name=row.name,
                        reference=row.reference,
                        version=row.version,
                        expected_digest=row.source_digest,
                        target_digest=row.target_digest,
                        target_repository=planned.target_repository,
                        final_reference=planned.final_reference,
                        status=row.status,
                        error_code=row.error_code,
                        error_message=row.error_message,
                    )
                    for index, (row, planned) in enumerate(
                        zip(rows, plan.artifacts, strict=True)
                    )
                ],
            )
            payload = receipt.model_dump_json(indent=2) + "\n"
            operation.import_receipt_json = receipt.model_dump_json()
            session.commit()

        self.receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.receipt_root / f"import-{operation_id}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o440)
            self._fsync_directory(self.receipt_root)
        except FileExistsError as exc:
            raise OperationTaskFailure(
                "import_receipt_exists",
                "Immutable import receipt уже существует",
            ) from exc
