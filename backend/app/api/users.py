from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.auth.dependencies import SessionDep, require_roles
from app.auth.security import hash_password
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository, UserRepository
from app.schemas.users import UserCreateRequest, UserResponse, UserUpdateRequest

router = APIRouter(prefix="/users", tags=["users"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
    )


def _audit_user_change(
    session: SessionDep,
    admin: User,
    *,
    event_type: str,
    target: User,
    changed_fields: list[str],
) -> None:
    AuditEventRepository(session).create(
        actor=admin,
        event_type=event_type,
        metadata={
            "target_user_id": target.id,
            "target_username": target.username,
            "changed_fields": sorted(changed_fields),
        },
    )


def _would_remove_active_admin(user: User, payload: UserUpdateRequest) -> bool:
    if user.role is not UserRole.ADMIN or not user.is_active:
        return False
    demotes_admin = payload.role is not None and payload.role is not UserRole.ADMIN
    disables_admin = payload.is_active is False
    return demotes_admin or disables_admin


def _ensure_active_admin_remains(
    session: SessionDep,
    user: User,
    payload: UserUpdateRequest,
) -> None:
    if not _would_remove_active_admin(user, payload):
        return
    active_admin_count = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    ) or 0
    if active_admin_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="at least one active admin is required",
        )


@router.get("", response_model=list[UserResponse])
def list_users(
    _admin: AdminDep,
    session: SessionDep,
) -> list[UserResponse]:
    return [_to_response(user) for user in UserRepository(session).list()]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    admin: AdminDep,
    session: SessionDep,
) -> UserResponse:
    repo = UserRepository(session)
    if repo.get_by_username(payload.username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already exists")
    user = repo.create(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    _audit_user_change(
        session,
        admin,
        event_type="user.created",
        target=user,
        changed_fields=["is_active", "password", "role"],
    )
    session.commit()
    return _to_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: AdminDep,
    session: SessionDep,
) -> UserResponse:
    repo = UserRepository(session)
    user = repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    _ensure_active_admin_remains(session, user, payload)

    changed_fields: list[str] = []
    if payload.role is not None and payload.role != user.role:
        user.role = payload.role
        changed_fields.append("role")
    if payload.is_active is not None and payload.is_active != user.is_active:
        user.is_active = payload.is_active
        changed_fields.append("is_active")
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        changed_fields.append("password")

    if changed_fields:
        _audit_user_change(
            session,
            admin,
            event_type="user.updated",
            target=user,
            changed_fields=changed_fields,
        )
    session.commit()
    return _to_response(user)
