from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import check_and_refresh_token, decode_access_token
from app.db.models import User, UserRole
from app.db.repositories import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


def get_db_session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db_session)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_current_user(
    request: Request,
    response: Response,
    credentials: CredentialsDep,
    session: SessionDep,
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    settings = request.app.state.settings
    secret = settings.jwt_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        )
    jwt_secret = secret.get_secret_value()
    try:
        user_id, new_token = check_and_refresh_token(
            credentials.credentials,
            jwt_secret,
            inactivity_timeout_minutes=settings.jwt_inactivity_timeout_minutes,
            lifetime_minutes=settings.jwt_access_token_minutes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        ) from exc

    # Set new token in response header if refreshed
    if new_token is not None:
        response.headers["X-New-Access-Token"] = new_token

    user = UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        )
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., User]:
    def dependency(user: CurrentUserDep) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient permissions",
            )
        return user

    return dependency
