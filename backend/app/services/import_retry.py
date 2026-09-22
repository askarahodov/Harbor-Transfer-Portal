from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.db.models import ArtifactResult, Operation
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.schemas.import_retries import ImportRetryResponse
from app.schemas.imports import ImportDestinationPlanRequest, ImportPreviewResponse
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator

_FAILURE_POLICY = "continue-on-error"


@dataclass(frozen=True, slots=True)
class _RetrySourceSnapshot:
    runtime_mode: str | None
    runtime_mode_version: int | None
    harbor_profile_id: str | None
    harbor_profile_name: str | None
    harbor_profile_url: str | None
    bundle_filename: str | None
    bundle_sha256: str | None
    bundle_size_bytes: int | None
    import_storage_key: str | None
    import_intake_mode: str | None
    source_delivery_id: str | None
    bundle_signing_key_fingerprint: str | None


class ImportRetryService:
    """Prepare a new import operation bound to the original frozen destination mapping."""

    def __init__(self, orchestrator: PolicyAwareImportDestinationPlanOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.session_factory = orchestrator.session_factory

    async def prepare_retry(
        self,
        original_operation_id: int,
        *,
        actor_user_id: int,
        actor_username: str,
        destination_plan_id: str,
    ) -> ImportRetryResponse:
        self.orchestrator._require_target()
        with self.session_factory() as session:
            original = session.get(Operation, original_operation_id)
            if original is None or original.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Исходная import operation не найдена",
                )
            if (
                original.status is not OperationStatus.FAILED
                or original.error_code != "import_partial_failure"
            ):
                raise ImportOrchestrationError(
                    "import_retry_not_allowed",
                    "Retry разрешён только для terminal import с partial failure",
                )
            if original.import_preview_json is None or original.import_policy_json is None:
                raise ImportOrchestrationError(
                    "import_retry_state_invalid",
                    "Исходная import operation не содержит persisted preview/plan",
                )

            policy = self.orchestrator._policy_object(original)
            original_plan = self.orchestrator._plan_from_policy(policy)
            if original_plan is None:
                raise ImportOrchestrationError(
                    "import_retry_state_invalid",
                    "Исходный immutable destination plan отсутствует",
                )
            if original_plan.plan_id != destination_plan_id:
                raise ImportOrchestrationError(
                    "import_retry_plan_mismatch",
                    "Retry должен использовать destination plan исходной operation",
                )
            try:
                frozen_mapping = ImportDestinationPlanRequest.model_validate(
                    policy.get("mapping_request", {})
                )
                preview = ImportPreviewResponse.model_validate_json(original.import_preview_json)
            except ValueError as exc:
                raise ImportOrchestrationError(
                    "import_retry_state_invalid",
                    "Persisted retry source state повреждён",
                ) from exc

            rows = sorted(original.artifacts, key=lambda item: item.id)
            planned = sorted(original_plan.artifacts, key=lambda item: item.index)
            if len(rows) != len(planned) or len(rows) != len(preview.artifacts):
                raise ImportOrchestrationError(
                    "import_retry_state_invalid",
                    "Persisted artifact outcome не совпадает с immutable destination plan",
                )
            for row, planned_item in zip(rows, planned, strict=True):
                if (
                    row.destination_plan_id != original_plan.plan_id
                    or row.destination_plan_hash != original_plan.plan_hash
                    or row.source_repository != planned_item.source_repository
                    or row.target_repository != planned_item.target_repository
                    or row.target_reference != planned_item.final_reference
                ):
                    raise ImportOrchestrationError(
                        "import_retry_state_invalid",
                        "Retry запрещён: persisted artifact snapshot не совпадает с plan",
                    )

            snapshot = _RetrySourceSnapshot(
                runtime_mode=original.runtime_mode,
                runtime_mode_version=original.runtime_mode_version,
                harbor_profile_id=original.harbor_profile_id,
                harbor_profile_name=original.harbor_profile_name,
                harbor_profile_url=original.harbor_profile_url,
                bundle_filename=original.bundle_filename,
                bundle_sha256=original.bundle_sha256,
                bundle_size_bytes=original.bundle_size_bytes,
                import_storage_key=original.import_storage_key,
                import_intake_mode=original.import_intake_mode,
                source_delivery_id=original.source_delivery_id,
                bundle_signing_key_fingerprint=original.bundle_signing_key_fingerprint,
            )

        # Fail before creating retry history if the original physical bundle is gone.
        self.orchestrator._bundle_paths(original_operation_id)

        with self.session_factory() as session:
            retry_operation = Operation(
                type=OperationType.IMPORT,
                status=OperationStatus.READY,
                runtime_mode=snapshot.runtime_mode,
                runtime_mode_version=snapshot.runtime_mode_version,
                harbor_profile_id=snapshot.harbor_profile_id,
                harbor_profile_name=snapshot.harbor_profile_name,
                harbor_profile_url=snapshot.harbor_profile_url,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                comment=(
                    f"Retry of import #{original_operation_id}; "
                    f"failure policy: {_FAILURE_POLICY}"
                ),
                bundle_filename=snapshot.bundle_filename,
                bundle_sha256=snapshot.bundle_sha256,
                bundle_size_bytes=snapshot.bundle_size_bytes,
                import_storage_key=snapshot.import_storage_key,
                import_intake_mode=snapshot.import_intake_mode,
                source_delivery_id=snapshot.source_delivery_id,
                bundle_signing_key_fingerprint=snapshot.bundle_signing_key_fingerprint,
                total_artifacts=len(preview.artifacts),
                progress_current=0,
                progress_total=len(preview.artifacts),
            )
            session.add(retry_operation)
            session.flush()
            retry_operation_id = retry_operation.id
            retry_preview = preview.model_copy(
                update={
                    "operation_id": retry_operation_id,
                    "status": OperationStatus.READY,
                    "overwrite_allowed": self.orchestrator.settings.import_allow_overwrite,
                }
            )
            retry_operation.import_preview_json = retry_preview.model_dump_json()
            for item in retry_preview.artifacts:
                session.add(
                    ArtifactResult(
                        operation_id=retry_operation_id,
                        artifact_type=item.artifact_type,
                        repository=item.repository,
                        name=item.name,
                        reference=item.reference,
                        version=item.version,
                        source_digest=item.expected_digest,
                        size_bytes=item.payload_size,
                        status=ArtifactStatus.PENDING,
                    )
                )
            session.commit()

        try:
            # Deliberately bypass policy-aware default resolution. The request persisted
            # with the original plan is already the frozen effective mapping snapshot.
            retry_plan = await ImportDestinationPlanOrchestrator._build_destination_plan(
                self.orchestrator,
                retry_operation_id,
                frozen_mapping,
                actor_username=actor_username,
            )
        except ImportOrchestrationError as exc:
            self._mark_prepare_failed(retry_operation_id, exc.code, exc.message)
            raise

        if retry_plan.plan_id != original_plan.plan_id:
            message = "Frozen mapping unexpectedly resolved to a different destination plan"
            self._mark_prepare_failed(retry_operation_id, "import_retry_plan_mismatch", message)
            raise ImportOrchestrationError("import_retry_plan_mismatch", message)

        with self.session_factory() as session:
            retry_operation = session.get(Operation, retry_operation_id)
            if retry_operation is None or retry_operation.import_policy_json is None:
                raise ImportOrchestrationError(
                    "import_retry_state_invalid",
                    "Retry operation lost its persisted destination plan",
                )
            retry_policy = self.orchestrator._policy_object(retry_operation)
            retry_policy["retry"] = {
                "of_operation_id": original_operation_id,
                "source_plan_id": original_plan.plan_id,
                "source_plan_hash": original_plan.plan_hash,
                "failure_policy": _FAILURE_POLICY,
            }
            retry_operation.import_policy_json = self.orchestrator._dump_policy(retry_policy)
            session.commit()

        return ImportRetryResponse(
            operation_id=retry_operation_id,
            retry_of_operation_id=original_operation_id,
            status=OperationStatus.READY,
            destination_plan=retry_plan,
        )

    def _mark_prepare_failed(self, operation_id: int, code: str, message: str) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                return
            operation.status = OperationStatus.FAILED
            operation.finished_at = datetime.now(UTC)
            operation.error_code = code
            operation.error_message = message
            session.commit()


def retry_of_operation_id(operation: Operation) -> int | None:
    if operation.type is not OperationType.IMPORT or operation.import_policy_json is None:
        return None
    try:
        payload = PolicyAwareImportDestinationPlanOrchestrator._policy_object(operation)
    except ImportOrchestrationError:
        return None
    retry = payload.get("retry")
    if not isinstance(retry, dict):
        return None
    value = retry.get("of_operation_id")
    return value if isinstance(value, int) and value > 0 else None
