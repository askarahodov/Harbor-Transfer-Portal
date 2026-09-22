from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.transfer_policy import TransferPolicyPatch, TransferPolicyResponse
from app.services.destination_mapping_policy import (
    DestinationMappingPolicyError,
    DestinationMappingPolicyService,
)
from app.services.transfer_policy import (
    POLICY_FIELDS,
    TransferPolicyError,
    TransferPolicyService,
)

router = APIRouter(prefix="/settings/transfer", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
_MAPPING_FIELDS = {
    "destination_container_image_project": "container_image_project",
    "destination_helm_chart_project": "helm_chart_project",
    "destination_project_mappings": "project_mappings",
}


def _service(request: Request, session: SessionDep) -> TransferPolicyService:
    return TransferPolicyService(session, request.app.state.settings)


def _response(service: TransferPolicyService, session: SessionDep) -> TransferPolicyResponse:
    snapshot = service.resolve()
    mapping = DestinationMappingPolicyService(session).resolve()
    return TransferPolicyResponse(
        import_allow_overwrite=snapshot.import_allow_overwrite,
        import_max_upload_bytes=snapshot.import_max_upload_bytes,
        bundle_max_archive_bytes=snapshot.bundle_max_archive_bytes,
        bundle_max_extracted_bytes=snapshot.bundle_max_extracted_bytes,
        bundle_max_member_count=snapshot.bundle_max_member_count,
        operation_disk_reserve_bytes=snapshot.operation_disk_reserve_bytes,
        operation_max_concurrent=snapshot.operation_max_concurrent,
        export_bundle_retention_seconds=snapshot.export_bundle_retention_seconds,
        import_bundle_retention_seconds=snapshot.import_bundle_retention_seconds,
        storage_cleanup_interval_seconds=snapshot.storage_cleanup_interval_seconds,
        effective_operation_max_concurrent=snapshot.effective_operation_max_concurrent,
        restart_required_fields=list(snapshot.restart_required_fields),
        destination_mapping_revision=mapping.revision,
        destination_container_image_project=mapping.container_image_project,
        destination_helm_chart_project=mapping.helm_chart_project,
        destination_project_mappings=mapping.project_mappings,
    )


@router.get("", response_model=TransferPolicyResponse)
def get_transfer_policy(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> TransferPolicyResponse:
    return _response(_service(request, session), session)


@router.patch("", response_model=TransferPolicyResponse)
def update_transfer_policy(
    payload: TransferPolicyPatch,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TransferPolicyResponse:
    supplied = payload.model_dump(exclude_unset=True)
    transfer_values = {
        field: value
        for field, value in supplied.items()
        if field in POLICY_FIELDS and value is not None
    }
    mapping_values = {
        internal: supplied[external]
        for external, internal in _MAPPING_FIELDS.items()
        if external in supplied
    }
    service = _service(request, session)
    mapping_service = DestinationMappingPolicyService(session)
    try:
        change = service.update(transfer_values)
        mapping_change = mapping_service.update(mapping_values)
    except (TransferPolicyError, DestinationMappingPolicyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    changed_fields = list(change.changed_fields) + [
        f"destination_{field}" for field in mapping_change.changed_fields
    ]
    if changed_fields:
        before: dict[str, object] = dict(change.before)
        after: dict[str, object] = dict(change.after)
        before.update(
            {f"destination_{key}": value for key, value in mapping_change.before.items()}
        )
        after.update(
            {f"destination_{key}": value for key, value in mapping_change.after.items()}
        )
        if mapping_change.changed_fields:
            before["destination_mapping_revision"] = mapping_change.snapshot.revision - 1
            after["destination_mapping_revision"] = mapping_change.snapshot.revision
        AuditEventRepository(session).create(
            actor=admin,
            event_type="transfer.policy.updated",
            metadata={
                "changed_fields": sorted(changed_fields),
                "before": before,
                "after": after,
                "restart_required_fields": list(change.snapshot.restart_required_fields),
            },
        )
    session.commit()
    service.apply_runtime(change.after)
    return _response(service, session)
