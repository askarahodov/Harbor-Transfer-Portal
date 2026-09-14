import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ArtifactResult, AuditEvent, Operation, SettingMetadata, User, UserRole
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.operations import validate_transition


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, username: str, password_hash: str, role: UserRole) -> User:
        normalized = username.strip().lower()
        if not normalized:
            raise ValueError("username must not be empty")
        user = User(username=normalized, password_hash=password_hash, role=role)
        self.session.add(user)
        self.session.flush()
        return user

    def get(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        normalized = username.strip().lower()
        return self.session.scalar(select(User).where(User.username == normalized))

    def list(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.username)))

    def mark_login(self, user: User) -> None:
        user.last_login_at = datetime.now(UTC)
        self.session.flush()


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

    def list_filtered(
        self,
        *,
        limit: int,
        offset: int,
        operation_type: OperationType | None = None,
        operation_status: OperationStatus | None = None,
        actor_username: str | None = None,
        delivery_id: str | None = None,
        search: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[Operation], int]:
        filters = []
        if operation_type is not None:
            filters.append(Operation.type == operation_type)
        if operation_status is not None:
            filters.append(Operation.status == operation_status)
        if actor_username:
            filters.append(Operation.actor_username == actor_username.strip().lower())
        if delivery_id:
            filters.append(Operation.delivery_id == delivery_id.strip())
        if created_from is not None:
            filters.append(Operation.created_at >= created_from)
        if created_to is not None:
            filters.append(Operation.created_at <= created_to)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    Operation.delivery_id.ilike(pattern),
                    Operation.actor_username.ilike(pattern),
                    Operation.comment.ilike(pattern),
                    Operation.error_code.ilike(pattern),
                )
            )

        base = select(Operation).where(*filters)
        total = self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = (
            base.order_by(Operation.created_at.desc(), Operation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt)), total

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


class SettingMetadataRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_value(self, key: str) -> str | None:
        item = self.session.get(SettingMetadata, key)
        return item.value if item is not None else None

    def set_value(self, key: str, value: str, *, description: str | None = None) -> None:
        item = self.session.get(SettingMetadata, key)
        if item is None:
            item = SettingMetadata(key=key, value=value, description=description)
            self.session.add(item)
        else:
            item.value = value
            if description is not None:
                item.description = description
        self.session.flush()


class AuditEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        actor: User,
        event_type: str,
        result: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_user_id=actor.id,
            actor_username=actor.username,
            event_type=event_type,
            result=result,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_filtered(
        self,
        *,
        limit: int,
        offset: int,
        event_type: str | None = None,
        result: str | None = None,
        actor_username: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[AuditEvent], int]:
        filters = []
        if event_type:
            filters.append(AuditEvent.event_type == event_type.strip())
        if result:
            filters.append(AuditEvent.result == result.strip())
        if actor_username:
            filters.append(AuditEvent.actor_username == actor_username.strip().lower())
        if created_from is not None:
            filters.append(AuditEvent.created_at >= created_from)
        if created_to is not None:
            filters.append(AuditEvent.created_at <= created_to)

        base = select(AuditEvent).where(*filters)
        total = self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = (
            base.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt)), total
