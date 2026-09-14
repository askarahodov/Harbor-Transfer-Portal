from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import SessionDep, require_roles
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository
from app.schemas.transfer_policy import TransferPolicyPatch, TransferPolicyResponse
from app.services.transfer_policy import TransferPolicyError, TransferPolicyService

router = APIRouter(prefix="/settings/transfer", tags=["settings"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _service(request: Request, session: SessionDep) -> TransferPolicyService:
    return TransferPolicyService(session, request.app.state.settings)


def _response(service: TransferPolicyService) -> TransferPolicyResponse:
    snapshot = service.resolve()
    return TransferPolicyResponse(
        import_allow_overwrite=snapshot.import_allow_overwrite,
        import_max_upload_bytes=snapshot.import_max_upload_bytes,
        bundle_max_archive_bytes=snapshot.bundle_max_archive_bytes,
        bundle_max_extracted_bytes=snapshot.bundle_max_extracted_bytes,
        bundle_max_member_count=snapshot.bundle_max_member_count,
        operation_disk_reserve_bytes=snapshot.operation_disk_reserve_bytes,
        operation_max_concurrent=snapshot.operation_max_concurrent,
        effective_operation_max_concurrent=snapshot.effective_operation_max_concurrent,
        restart_required_fields=list(snapshot.restart_required_fields),
    )


@router.get("", response_model=TransferPolicyResponse)
def get_transfer_policy(
    request: Request,
    _admin: AdminDep,
    session: SessionDep,
) -> TransferPolicyResponse:
    return _response(_service(request, session))


@router.patch("", response_model=TransferPolicyResponse)
def update_transfer_policy(
    payload: TransferPolicyPatch,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> TransferPolicyResponse:
    values = {
        field: value
        for field, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    service = _service(request, session)
    try:
        change = service.update(values)
    except TransferPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    if change.changed_fields:
        AuditEventRepository(session).create(
            actor=admin,
            event_type="transfer.policy.updated",
            metadata={
                "changed_fields": list(change.changed_fields),
                "before": change.before,
                "after": change.after,
                "restart_required_fields": list(change.snapshot.restart_required_fields),
            },
        )
    session.commit()
    return _response(service)
