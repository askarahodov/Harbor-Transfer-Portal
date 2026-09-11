from pydantic import BaseModel, Field

from app.db.models import UserRole


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=4096)
    role: UserRole


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=4096)


class UserResponse(BaseModel):
    id: int
    username: str
    role: UserRole
    is_active: bool
