from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_initial_admin_password
from app.db.models import User, UserRole
from app.db.repositories import AuditEventRepository, UserRepository


def bootstrap_first_run_admin(session: Session) -> bool:
    """Create the temporary admin only when the entire user table is empty."""
    if session.scalar(select(User.id).limit(1)) is not None:
        return False
    admin = UserRepository(session).create(
        username="admin",
        password_hash=hash_initial_admin_password(),
        role=UserRole.ADMIN,
    )
    admin.must_change_password = True
    AuditEventRepository(session).create_system(
        event_type="auth.bootstrap.created",
        result="success",
        metadata={"user_id": admin.id, "must_change_password": True},
    )
    session.commit()
    return True
