from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.auth.dependencies import CurrentUserDep, SessionDep
from app.db.models import Operation, UserRole
from app.db.repositories import AuditEventRepository, OperationRepository
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.operations import TERMINAL_STATES
from app.schemas.import_retries import retry_lineage_from_policy
from app.schemas.operations import (
    OperationArtifactResponse,
    OperationBundleResponse,
    OperationListResponse,
    OperationProgressResponse,
    OperationResponse,
    OperationSummaryResponse,
)
from app.services.operation_manager import OperationManager, OperationManagerError
from app.services.report_service import build_operation_pdf, iter_operation_csv, report_filename

router = APIRouter(prefix="/operations", tags=["operations"])


def _manager(request: Request) -> OperationManager:
    return cast(OperationManager, request.app.state.operation_manager)


def _serialize_bundle(operation: Operation) -> OperationBundleResponse | None:
    if (
        operation.bundle_filename is None
        or operation.bundle_sha256 is None
        or operation.bundle_size_bytes is None
    ):
        return None
    return OperationBundleResponse(
        filename=operation.bundle_filename,
        size_bytes=operation.bundle_size_bytes,
        sha256=operation.bundle_sha256,
    )


def _serialize_summary(operation: Operation) -> OperationSummaryResponse:
    lineage = retry_lineage_from_policy(operation.import_policy_json)
    return OperationSummaryResponse(
        id=operation.id,
        delivery_id=operation.delivery_id,
        type=operation.type,
        status=operation.status,
        actor_username=operation.actor_username,
        comment=operation.comment,
        retry_of_operation_id=(lineage.retry_of_operation_id if lineage else None),
        failure_policy=(lineage.failure_policy if lineage else None),
        created_at=operation.created_at,
        started_at=operation.started_at,
        finished_at=operation.finished_at,
        error_code=operation.error_code,
        error_message=operation.error_message,
        total_artifacts=operation.total_artifacts,
        successful_artifacts=operation.successful_artifacts,
        failed_artifacts=operation.failed_artifacts,
        skipped_artifacts=operation.skipped_artifacts,
        conflict_artifacts=operation.conflict_artifacts,
        bundle=_serialize_bundle(operation),
    )


def _serialize_operation(operation: Operation) -> OperationResponse:
    artifacts = sorted(operation.artifacts, key=lambda item: item.id)
    running = [artifact.id for artifact in artifacts if artifact.status is ArtifactStatus.RUNNING]
    completed = sum(
        artifact.status
        in {
            ArtifactStatus.IMPORTED,
            ArtifactStatus.SKIPPED,
            ArtifactStatus.CONFLICT,
            ArtifactStatus.FAILED,
            ArtifactStatus.VERIFIED,
        }
        for artifact in artifacts
    )
    lineage = retry_lineage_from_policy(operation.import_policy_json)
    return OperationResponse(
        id=operation.id,
        delivery_id=operation.delivery_id,
        type=operation.type,
        status=operation.status,
        actor_username=operation.actor_username,
        comment=operation.comment,
        retry_of_operation_id=(lineage.retry_of_operation_id if lineage else None),
        failure_policy=(lineage.failure_policy if lineage else None),
        started_at=operation.started_at,
        finished_at=operation.finished_at,
        error_code=operation.error_code,
        error_message=operation.error_message,
        cancel_requested=operation.cancel_requested_at is not None,
        bundle=_serialize_bundle(operation),
        progress=OperationProgressResponse(
            total_artifacts=operation.total_artifacts,
            completed_artifacts=completed,
            running_artifacts=len(running),
            successful_artifacts=operation.successful_artifacts,
            failed_artifacts=operation.failed_artifacts,
            skipped_artifacts=operation.skipped_artifacts,
            conflict_artifacts=operation.conflict_artifacts,
            progress_current=operation.progress_current,
            progress_total=operation.progress_total,
            current_phase=operation.status,
            running_artifact_ids=running,
        ),
        artifacts=[
            OperationArtifactResponse(
                id=artifact.id,
                artifact_type=artifact.artifact_type,
                repository=artifact.repository,
                name=artifact.name,
                reference=artifact.reference,
                version=artifact.version,
                source_digest=artifact.source_digest,
                target_digest=artifact.target_digest,
                source_project=artifact.source_project,
                source_repository=artifact.source_repository,
                source_reference=artifact.source_reference,
                source_version=artifact.source_version,
                target_project=artifact.target_project,
                target_repository=artifact.target_repository,
                target_reference=artifact.target_reference,
                target_version=artifact.target_version,
                destination_plan_id=artifact.destination_plan_id,
                destination_plan_hash=artifact.destination_plan_hash,
                overwrite_approved=artifact.overwrite_approved,
                status=artifact.status,
                error_code=artifact.error_code,
                error_message=artifact.error_message,
                size_bytes=artifact.size_bytes,
                started_at=artifact.started_at,
                finished_at=artifact.finished_at,
            )
            for artifact in artifacts
        ],
    )


def _report_operation(session: SessionDep, operation_id: int) -> Operation:
    operation = OperationRepository(session).get(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    if operation.status not in TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "operation_report_not_ready",
                "message": "Report is available only after the operation reaches a terminal state",
            },
        )
    return operation


@router.get("", response_model=OperationListResponse)
def list_operations(
    _user: CurrentUserDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    operation_type: Annotated[OperationType | None, Query(alias="type")] = None,
    operation_status: Annotated[OperationStatus | None, Query(alias="status")] = None,
    actor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    delivery_id: Annotated[str | None, Query(min_length=1, max_length=96)] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
) -> OperationListResponse:
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="created_from must be earlier than or equal to created_to",
        )
    items, total = OperationRepository(session).list_filtered(
        limit=limit,
        offset=offset,
        operation_type=operation_type,
        operation_status=operation_status,
        actor_username=actor,
        delivery_id=delivery_id,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )
    return OperationListResponse(
        items=[_serialize_summary(operation) for operation in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{operation_id}", response_model=OperationResponse)
def get_operation(
    operation_id: int,
    _user: CurrentUserDep,
    session: SessionDep,
) -> OperationResponse:
    operation = OperationRepository(session).get(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    return _serialize_operation(operation)


@router.get("/{operation_id}/report.csv")
def download_operation_csv(
    operation_id: int,
    _user: CurrentUserDep,
    session: SessionDep,
) -> StreamingResponse:
    operation = _report_operation(session, operation_id)
    filename = report_filename(operation_id, "csv")
    return StreamingResponse(
        iter_operation_csv(operation),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{operation_id}/report.pdf")
def download_operation_pdf(
    operation_id: int,
    request: Request,
    _user: CurrentUserDep,
    session: SessionDep,
) -> StreamingResponse:
    operation = _report_operation(session, operation_id)
    stream = build_operation_pdf(operation, request.app.state.settings.portal_contour)
    filename = report_filename(operation_id, "pdf")
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(stream.close),
    )


@router.post("/{operation_id}/cancel", response_model=OperationResponse)
async def cancel_operation(
    operation_id: int,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> OperationResponse:
    operation = OperationRepository(session).get(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    if user.role is UserRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient permissions",
        )
    if user.role is not UserRole.ADMIN and operation.actor_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only operation creator or admin may cancel operation",
        )

    was_terminal = operation.status in TERMINAL_STATES
    try:
        await _manager(request).cancel(operation_id)
    except OperationManagerError as exc:
        if exc.code == "operation_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="operation not found",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc

    if not was_terminal:
        metadata: dict[str, object] = {
            "operation_id": operation.id,
            "operation_type": operation.type.value,
        }
        if operation.delivery_id:
            metadata["delivery_id"] = operation.delivery_id
        if operation.source_delivery_id:
            metadata["source_delivery_id"] = operation.source_delivery_id
        AuditEventRepository(session).create(
            actor=user,
            event_type=(
                "export.cancel.requested"
                if operation.type is OperationType.EXPORT
                else "import.cancel.requested"
            ),
            result="requested",
            metadata=metadata,
        )
        session.commit()

    session.expire_all()
    refreshed = OperationRepository(session).get(operation_id)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation not found")
    return _serialize_operation(refreshed)
