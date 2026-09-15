from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.harbor import HarborClientDep, _harbor_error
from app.auth.dependencies import SessionDep, require_roles
from app.config import PortalContour
from app.db.models import Operation, User, UserRole
from app.db.repositories import AuditEventRepository
from app.domain.bundle import OperationType
from app.schemas.harbor import HarborProjectCreateRequest, HarborProjectCreateResponse
from app.services.harbor_client import HarborClient, HarborClientError
from app.services.runtime_mode import RuntimeModeService

router = APIRouter(prefix="/harbor", tags=["harbor"])
AdminUserDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _project_exists(client: HarborClient, project: str) -> bool:
    page = client.list_projects_page(1, 100, search_needle=project)
    return any(item.name == project for item in page.items)


def _correlated_import(session: SessionDep, operation_id: int | None) -> None:
    if operation_id is None:
        return
    operation = session.get(Operation, operation_id)
    if operation is None or operation.type is not OperationType.IMPORT:
        raise _api_error(
            status.HTTP_404_NOT_FOUND,
            "import_operation_not_found",
            "Import-операция для project creation correlation не найдена",
        )


def _audit_project_create(
    session: SessionDep,
    actor: User,
    *,
    client: HarborClient,
    payload: HarborProjectCreateRequest,
    result: str,
    error_code: str | None = None,
) -> None:
    metadata: dict[str, object] = {
        "local_harbor": urlsplit(client.base_url).netloc,
        "project": payload.name,
        "public": payload.public,
    }
    if payload.operation_id is not None:
        metadata["operation_id"] = payload.operation_id
    if error_code is not None:
        metadata["error_code"] = error_code
    AuditEventRepository(session).create(
        actor=actor,
        event_type="harbor.project.create",
        result=result,
        metadata=metadata,
    )
    session.commit()


@router.post("/projects", response_model=HarborProjectCreateResponse)
def create_project(
    payload: HarborProjectCreateRequest,
    request: Request,
    actor: AdminUserDep,
    session: SessionDep,
    client: HarborClientDep,
) -> HarborProjectCreateResponse:
    """Explicit admin-only TARGET Harbor project creation.

    This endpoint never starts or resumes an import. The caller must rebuild destination
    validation after a successful or idempotent result.
    """
    _correlated_import(session, payload.operation_id)
    runtime = RuntimeModeService(session, request.app.state.settings)
    with runtime.mode_guard(PortalContour.TARGET):
        try:
            if _project_exists(client, payload.name):
                _audit_project_create(
                    session,
                    actor,
                    client=client,
                    payload=payload,
                    result="exists",
                )
                return HarborProjectCreateResponse(
                    name=payload.name,
                    public=payload.public,
                    created=False,
                )

            try:
                client.create_project(payload.name, public=payload.public)
            except HarborClientError as exc:
                if exc.code == "conflict" and _project_exists(client, payload.name):
                    _audit_project_create(
                        session,
                        actor,
                        client=client,
                        payload=payload,
                        result="exists",
                    )
                    return HarborProjectCreateResponse(
                        name=payload.name,
                        public=payload.public,
                        created=False,
                    )
                raise
        except HarborClientError as exc:
            _audit_project_create(
                session,
                actor,
                client=client,
                payload=payload,
                result="failure",
                error_code=exc.code,
            )
            raise _harbor_error(exc) from exc

        _audit_project_create(
            session,
            actor,
            client=client,
            payload=payload,
            result="created",
        )
        return HarborProjectCreateResponse(
            name=payload.name,
            public=payload.public,
            created=True,
        )
