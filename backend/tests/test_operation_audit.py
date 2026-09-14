import json
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db.base import Base
from app.db.models import AuditEvent, Operation, UserRole
from app.db.repositories import UserRepository
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services.operation_audit import install_operation_audit_hooks


def _session_factory(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'operation-audit.db'}",
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    install_operation_audit_hooks()
    return create_session_factory(engine)


def _events(session, operation_id: int) -> list[AuditEvent]:  # type: ignore[no-untyped-def]
    events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    return [
        event
        for event in events
        if json.loads(event.metadata_json).get("operation_id") == operation_id
    ]


def test_export_create_and_terminal_failure_are_audited(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path)
    with factory() as session:
        actor = UserRepository(session).create(
            username="operator",
            password_hash="not-used-in-this-test",
            role=UserRole.OPERATOR,
        )
        operation = Operation(
            delivery_id="DELIVERY-20260914-AUDIT01",
            type=OperationType.EXPORT,
            status=OperationStatus.CREATED,
            actor_user_id=actor.id,
            actor_username=actor.username,
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

        created_events = _events(session, operation_id)
        assert [(event.event_type, event.actor_username) for event in created_events] == [
            ("export.created", "operator")
        ]

        operation.status = OperationStatus.FAILED
        operation.error_code = "export_source_changed"
        operation.error_message = "password=must-never-be-audited"
        session.commit()

        events = _events(session, operation_id)
        assert [event.event_type for event in events] == ["export.created", "export.failed"]
        terminal = events[-1]
        assert terminal.actor_username == "system"
        assert terminal.result == "failure"
        metadata = json.loads(terminal.metadata_json)
        assert metadata["error_code"] == "export_source_changed"
        assert "error_message" not in metadata
        assert "must-never-be-audited" not in terminal.metadata_json


def test_import_verification_and_completion_are_audited_once(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path)
    with factory() as session:
        actor = UserRepository(session).create(
            username="operator",
            password_hash="not-used-in-this-test",
            role=UserRole.OPERATOR,
        )
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.UPLOADED,
            actor_user_id=actor.id,
            actor_username=actor.username,
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

        operation.status = OperationStatus.VERIFYING
        session.commit()
        operation.source_delivery_id = "DELIVERY-20260914-SOURCE01"
        operation.status = OperationStatus.READY
        session.commit()
        operation.status = OperationStatus.IMPORTING
        session.commit()
        operation.status = OperationStatus.VERIFYING_TARGET
        session.commit()
        operation.status = OperationStatus.COMPLETED
        session.commit()

        events = _events(session, operation_id)
        assert [event.event_type for event in events] == [
            "import.intake.created",
            "import.verification.started",
            "import.verification.succeeded",
            "import.completed",
        ]
        assert events[0].actor_username == "operator"
        assert events[1].actor_username == "operator"
        assert events[2].actor_username == "system"
        assert events[3].actor_username == "system"
        assert json.loads(events[-1].metadata_json)["source_delivery_id"] == (
            "DELIVERY-20260914-SOURCE01"
        )
