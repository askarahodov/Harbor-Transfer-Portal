from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ArtifactResult, Operation
from app.domain.bundle import OperationType
from app.schemas.imports import ImportDestinationArtifactPlanResponse, ImportDestinationPlanResponse
from app.services.operation_manager import OperationTaskFailure


def persist_artifact_mapping_snapshot(
    session: Session,
    operation: Operation,
    plan: ImportDestinationPlanResponse,
    *,
    overwrite_approved: bool,
) -> None:
    """Bind immutable source→target plan data to persisted artifact outcome rows.

    The caller owns the transaction and must commit this snapshot before any Harbor
    mutation. Historical reporting must only read these persisted fields afterwards.
    """
    if (
        not plan.valid
        or operation.type is not OperationType.IMPORT
        or operation.id != plan.operation_id
        or operation.bundle_sha256 != plan.bundle_sha256
        or operation.source_delivery_id != plan.source_delivery_id
    ):
        raise OperationTaskFailure(
            "import_destination_snapshot_invalid",
            "Destination snapshot не связан с valid plan текущей import operation",
        )

    rows = sorted(operation.artifacts, key=lambda item: item.id)
    planned = sorted(plan.artifacts, key=lambda item: item.index)
    if len(rows) != len(planned):
        raise OperationTaskFailure(
            "import_destination_snapshot_invalid",
            "Destination plan не совпадает с persisted artifact rows",
        )

    pairs = list(enumerate(zip(rows, planned, strict=True)))
    for expected_index, (row, item) in pairs:
        _validate_source_row(expected_index, row, item)
        _validate_target(item)

    for _expected_index, (row, item) in pairs:
        row.source_project = item.source_project
        row.source_repository = item.source_repository
        row.source_reference = item.reference
        row.source_version = item.version
        row.target_project = item.target_project
        row.target_repository = item.target_repository
        row.target_reference = item.final_reference
        row.target_version = item.version
        row.destination_plan_id = plan.plan_id
        row.destination_plan_hash = plan.plan_hash
        row.overwrite_approved = overwrite_approved

    session.flush()


def _validate_source_row(
    expected_index: int,
    row: ArtifactResult,
    item: ImportDestinationArtifactPlanResponse,
) -> None:
    if (
        item.index != expected_index
        or row.artifact_type != item.artifact_type
        or row.repository != item.source_repository
        or row.source_digest != item.expected_digest
        or row.name != item.name
        or row.reference != item.reference
        or row.version != item.version
    ):
        raise OperationTaskFailure(
            "import_destination_snapshot_invalid",
            "Destination plan source identity не совпадает с persisted artifact row",
        )


def _validate_target(item: ImportDestinationArtifactPlanResponse) -> None:
    if (
        item.target_project is None
        or item.target_repository is None
        or item.final_reference is None
    ):
        raise OperationTaskFailure(
            "import_destination_snapshot_invalid",
            "Destination plan не содержит полный TARGET reference",
        )
