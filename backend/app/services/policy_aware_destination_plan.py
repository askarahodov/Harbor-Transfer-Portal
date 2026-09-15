from __future__ import annotations

from app.db.models import Operation
from app.domain.bundle import OperationType
from app.schemas.imports import ImportDestinationPlanRequest, ImportDestinationPlanResponse
from app.services.artifact_mapping_snapshot import persist_artifact_mapping_snapshot
from app.services.destination_mapping_policy import DestinationMappingPolicyService
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator
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
        super()._persist_destination_plan(operation_id, mapping, plan)
