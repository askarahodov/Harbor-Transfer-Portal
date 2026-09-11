from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.db.models import User, UserRole
from app.db.repositories import UserRepository


def bootstrap_admin(session: Session, *, username: str, password: str) -> tuple[User, bool]:
    repo = UserRepository(session)
    existing = repo.get_by_username(username)
    if existing is not None:
        return existing, False

    user = repo.create(
        username=username,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    session.commit()
    return user, True
