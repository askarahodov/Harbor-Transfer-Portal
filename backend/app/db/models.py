from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class LoginThrottle(TimestampMixin, Base):
    __tablename__ = "login_throttles"
    __table_args__ = (
        UniqueConstraint("scope", "subject_hash", name="uq_login_throttles_scope_subject_hash"),
        CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(index=True, nullable=True)


class Operation(TimestampMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint("progress_current >= 0", name="progress_current_nonnegative"),
        CheckConstraint("progress_total >= 0", name="progress_total_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str | None] = mapped_column(String(96), unique=True, nullable=True)
    type: Mapped[OperationType] = mapped_column(
        Enum(OperationType, native_enum=False),
        nullable=False,
    )
    status: Mapped[OperationStatus] = mapped_column(
        Enum(OperationStatus, native_enum=False),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_username: Mapped[str] = mapped_column(String(128), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_artifacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_artifacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_artifacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_artifacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifacts: Mapped[list["ArtifactResult"]] = relationship(
        back_populates="operation", cascade="all, delete-orphan"
    )


class ArtifactResult(Base):
    __tablename__ = "artifact_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    repository: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[ArtifactStatus] = mapped_column(
        Enum(ArtifactStatus, native_enum=False),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    operation: Mapped[Operation] = relationship(back_populates="artifacts")


class SettingMetadata(TimestampMixin, Base):
    __tablename__ = "setting_metadata"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
