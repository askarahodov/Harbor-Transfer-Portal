import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth.dependencies import CurrentUserDep, SessionDep
from app.auth.rate_limit import LoginRateLimitDecision, LoginRateLimiter
from app.auth.security import create_access_token, hash_password, verify_login_password
from app.db.repositories import AuditEventRepository, UserRepository
from app.schemas.auth import ChangePasswordRequest, CurrentUserResponse, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _invalid_credentials(decision: LoginRateLimitDecision | None = None) -> HTTPException:
    if decision is not None and decision.blocked:
        scope = decision.scope.value if decision.scope is not None else "unknown"
        fingerprint = (decision.subject_fingerprint or "unknown")[:12]
        logger.warning(
            "Login throttled scope=%s subject=%s retry_after_seconds=%s",
            scope,
            fingerprint,
            decision.retry_after_seconds,
        )
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")


def _audit_login_failure(
    session: SessionDep,
    *,
    reason: str,
    decision: LoginRateLimitDecision | None = None,
) -> None:
    metadata = {"reason": reason}
    if decision is not None and decision.scope is not None:
        metadata["scope"] = decision.scope.value
    AuditEventRepository(session).create_system(
        event_type="auth.login.failed",
        result="failure",
        metadata=metadata,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
) -> TokenResponse:
    settings = request.app.state.settings
    if settings.jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        )

    jwt_secret = settings.jwt_secret.get_secret_value()
    client_address = request.client.host if request.client is not None else None
    limiter = LoginRateLimiter(
        session,
        secret=jwt_secret,
        window_seconds=settings.login_rate_limit_window_seconds,
        username_max_failures=settings.login_rate_limit_username_max_failures,
        address_max_failures=settings.login_rate_limit_address_max_failures,
        lockout_seconds=settings.login_rate_limit_lockout_seconds,
    )
    decision = limiter.check(username=payload.username, client_address=client_address)
    if decision.blocked:
        _audit_login_failure(session, reason="rate_limited", decision=decision)
        session.commit()
        raise _invalid_credentials(decision)

    repo = UserRepository(session)
    user = repo.get_by_username(payload.username)
    password_hash = user.password_hash if user is not None and user.is_active else None
    password_valid = verify_login_password(payload.password, password_hash)
    if user is None or not user.is_active or not password_valid:
        decision = limiter.register_failure(
            username=payload.username,
            client_address=client_address,
        )
        _audit_login_failure(session, reason="invalid_credentials", decision=decision)
        session.commit()
        raise _invalid_credentials(decision)

    limiter.register_success(username=payload.username)
    repo.mark_login(user)
    AuditEventRepository(session).create(
        actor=user,
        event_type="auth.login.succeeded",
        metadata={"role": user.role.value},
    )
    session.commit()
    token = create_access_token(
        user_id=user.id,
        secret=jwt_secret,
        token_version=user.token_version,
        lifetime_minutes=max(
            settings.jwt_access_token_minutes,
            settings.jwt_inactivity_timeout_minutes,
        ),
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUserResponse)
def me(user: CurrentUserDep) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
    )


@router.post("/activity", status_code=status.HTTP_204_NO_CONTENT)
def renew_active_session(
    user: CurrentUserDep,
    request: Request,
    response: Response,
) -> None:
    """Renew only after explicit user interaction; passive API polling is not activity."""
    settings = request.app.state.settings
    secret = settings.jwt_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        )
    response.headers["X-New-Access-Token"] = create_access_token(
        user_id=user.id,
        secret=secret.get_secret_value(),
        token_version=user.token_version,
        lifetime_minutes=max(
            settings.jwt_access_token_minutes,
            settings.jwt_inactivity_timeout_minutes,
        ),
    )
    response.headers["Cache-Control"] = "no-store"



@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUserDep,
    session: SessionDep,
) -> Response:
    if not verify_login_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_current_password"
        )
    if payload.new_password == "admin" or payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_new_password"
        )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.token_version += 1
    AuditEventRepository(session).create(
        actor=user, event_type="auth.password.changed",
        metadata={"self_service": True, "tokens_revoked": True},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUserDep, session: SessionDep) -> Response:
    user.token_version += 1
    AuditEventRepository(session).create(actor=user, event_type="auth.logout")
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
