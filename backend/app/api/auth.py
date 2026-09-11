from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_db_session
from app.auth.security import create_access_token, verify_password
from app.db.models import User
from app.db.repositories import UserRepository
from app.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> TokenResponse:
    settings = request.app.state.settings
    if settings.jwt_secret is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="authentication is not configured")

    repo = UserRepository(session)
    user = repo.get_by_username(payload.username)
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    repo.mark_login(user)
    session.commit()
    token = create_access_token(
        user_id=user.id,
        secret=settings.jwt_secret.get_secret_value(),
        lifetime_minutes=settings.jwt_access_token_minutes,
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUserResponse)
def me(user: User = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
    )
