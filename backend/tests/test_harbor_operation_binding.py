from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.services.harbor_profiles import HarborProfileService
from app.services.harbor_settings import HarborSettingsError


def _environment(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'profiles-binding.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        harbor_url="https://default.harbor.local",
        harbor_managed_secret_file=tmp_path / "secrets" / "harbor-password",
        harbor_managed_ca_file=tmp_path / "secrets" / "harbor-ca.pem",
    )
    with sessions() as session:
        service = HarborProfileService(session, settings)
        first = service.create(
            name="Harbor A",
            url="https://harbor-a.local",
            username="svc-a",
            verify_tls=True,
            enabled=True,
        )
        second = service.create(
            name="Harbor B",
            url="https://harbor-b.local",
            username="svc-b",
            verify_tls=True,
            enabled=True,
        )
        session.commit()
        first_id = first.id
        second_id = second.id
    return settings, sessions, first_id, second_id


def _operation(session) -> Operation:
    operation = Operation(
        type=OperationType.IMPORT,
        status=OperationStatus.READY,
        actor_username="operator",
    )
    session.add(operation)
    session.flush()
    return operation


def test_operation_profile_binding_is_immutable(tmp_path: Path) -> None:
    settings, sessions, first_id, second_id = _environment(tmp_path)

    with sessions() as session:
        service = HarborProfileService(session, settings)
        operation = _operation(session)

        bound = service.bind_operation_profile(operation, first_id)
        assert bound.id == first_id
        assert operation.harbor_profile_id == first_id
        assert operation.harbor_profile_name == "Harbor A"
        assert operation.harbor_profile_url == "https://harbor-a.local"

        same = service.bind_operation_profile(operation, None)
        assert same.id == first_id

        with pytest.raises(HarborSettingsError) as exc_info:
            service.bind_operation_profile(operation, second_id)
        assert exc_info.value.code == "harbor_profile_selection_locked"


def test_bound_profile_mutation_is_blocked_while_operation_nonterminal(tmp_path: Path) -> None:
    settings, sessions, first_id, _second_id = _environment(tmp_path)

    with sessions() as session:
        service = HarborProfileService(session, settings)
        operation = _operation(session)
        service.bind_operation_profile(operation, first_id)
        session.commit()

    with sessions() as session:
        service = HarborProfileService(session, settings)
        with pytest.raises(HarborSettingsError) as update_error:
            service.update(first_id, url="https://changed.harbor.local")
        assert update_error.value.code == "harbor_profile_busy"
        session.rollback()

        with pytest.raises(HarborSettingsError) as credential_error:
            service.rotate_credential(first_id, "blocked-secret")
        assert credential_error.value.code == "harbor_profile_busy"


def test_terminal_evidence_blocks_delete_and_detects_identity_drift(tmp_path: Path) -> None:
    settings, sessions, first_id, _second_id = _environment(tmp_path)

    with sessions() as session:
        service = HarborProfileService(session, settings)
        operation = _operation(session)
        service.bind_operation_profile(operation, first_id)
        operation.status = OperationStatus.COMPLETED
        session.commit()

    with sessions() as session:
        service = HarborProfileService(session, settings)
        updated = service.update(first_id, url="https://changed.harbor.local")
        assert updated.url == "https://changed.harbor.local"
        session.commit()

        operation = session.query(Operation).one()
        with pytest.raises(HarborSettingsError) as drift:
            service.assert_operation_binding(operation)
        assert drift.value.code == "harbor_profile_changed"

        with pytest.raises(HarborSettingsError) as delete_error:
            service.delete(first_id)
        assert delete_error.value.code == "harbor_profile_in_use"


def test_disabled_profile_is_rejected_for_new_binding(tmp_path: Path) -> None:
    settings, sessions, first_id, _second_id = _environment(tmp_path)

    with sessions() as session:
        service = HarborProfileService(session, settings)
        service.update(first_id, enabled=False)
        operation = _operation(session)

        with pytest.raises(HarborSettingsError) as exc_info:
            service.bind_operation_profile(operation, first_id)
        assert exc_info.value.code == "harbor_profile_disabled"
