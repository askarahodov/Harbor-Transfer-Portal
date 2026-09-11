from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ArtifactResult, Operation, User
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.operations import validate_transition


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, username: str, password_hash: str, role) -> User:  # type: ignore[no-untyped-def]
        normalized = username.strip().lower()
        if not normalized:
            raise ValueError("username must not be empty")
        user = User(username=normalized, password_hash=password_hash, role=role)
        self.session.add(user)
        self.session.flush()
        return user


class OperationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        operation_type: OperationType,
        status: OperationStatus,
        actor: User | None,
        actor_username: str,
        comment: str | None = None,
    ) -> Operation:
        operation = Operation(
            type=operation_type,
            status=status,
            actor_user_id=actor.id if actor else None,
            actor_username=actor_username,
            comment=comment,
        )
        self.session.add(operation)
        self.session.flush()
        return operation

    def get(self, operation_id: int) -> Operation | None:
        stmt = (
            select(Operation)
            .where(Operation.id == operation_id)
            .options(selectinload(Operation.artifacts))
        )
        return self.session.scalar(stmt)

    def transition(self, operation: Operation, new_status: OperationStatus) -> None:
        validate_transition(operation.type, operation.status, new_status)
        operation.status = new_status
        self.session.flush()

    def add_artifact(
        self,
        operation: Operation,
        *,
        artifact_type: str,
        repository: str,
        status: ArtifactStatus = ArtifactStatus.PENDING,
        reference: str | None = None,
        version: str | None = None,
        name: str | None = None,
        source_digest: str | None = None,
    ) -> ArtifactResult:
        artifact = ArtifactResult(
            operation=operation,
            artifact_type=artifact_type,
            repository=repository,
            status=status,
            reference=reference,
            version=version,
            name=name,
            source_digest=source_digest,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact
