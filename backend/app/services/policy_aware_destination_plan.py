from __future__ import annotations

from app.schemas.imports import ImportDestinationPlanRequest, ImportDestinationPlanResponse
from app.services.destination_mapping_policy import DestinationMappingPolicyService
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator


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
