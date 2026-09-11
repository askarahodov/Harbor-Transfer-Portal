from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import SessionDep, require_roles
from app.auth.security import hash_password
from app.db.models import User, UserRole
from app.db.repositories import UserRepository
from app.schemas.users import UserCreateRequest, UserResponse, UserUpdateRequest

router = APIRouter(prefix="/users", tags=["users"])
AdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
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
    _admin: AdminDep,
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
    session.commit()
    return _to_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    _admin: AdminDep,
    session: SessionDep,
) -> UserResponse:
    user = UserRepository(session).get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    session.commit()
    return _to_response(user)
