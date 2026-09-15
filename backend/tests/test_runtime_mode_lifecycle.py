from __future__ import annotations

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.models import AuditEvent, SettingMetadata, User, UserRole
from app.db.repositories import UserRepository
from app.main import create_app
from app.services.runtime_mode import RuntimeModeService

JWT_SECRET = "runtime-mode-lifecycle-test-secret-" + "x" * 32


def _alembic(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _settings(database_url: str, contour: PortalContour) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
        portal_contour=contour,
    )


def _seed_operator(app) -> None:  # type: ignore[no-untyped-def]
    with app.state.session_factory() as session:
        repository = UserRepository(session)
        if repository.get_by_username("operator") is None:
            repository.create(
                username="operator",
                password_hash=hash_password("operator-password-123"),
                role=UserRole.OPERATOR,
            )
            session.commit()


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _metadata(app) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with app.state.session_factory() as session:
        rows = session.scalars(
            select(SettingMetadata).where(
                SettingMetadata.key.in_(
                    ["runtime.portal_mode", "runtime.portal_mode_version"]
                )
            )
        )
        return {row.key: row.value for row in rows}


def _switch(
    app,  # type: ignore[no-untyped-def]
    actor_id: int,
    target: PortalContour,
):  # type: ignore[no-untyped-def]
    with app.state.session_factory() as session:
        actor = session.get(User, actor_id)
        assert actor is not None
        return RuntimeModeService(session, app.state.settings).switch(target, actor=actor)


def _export_selection() -> dict[str, object]:
    return {
        "artifacts": [
            {
                "kind": "container-image",
                "project": "team",
                "repository": "apps/demo",
                "reference": "1.0.0",
                "digest": "sha256:" + "a" * 64,
            }
        ]
    }


def test_upgrade_from_0006_adds_operation_snapshot_and_bootstraps_runtime_metadata(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic(database_url)
    command.upgrade(config, "0006_import_orchestration_metadata")

    with sqlite3.connect(database_path) as connection:
        before_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)").fetchall()
        }
    assert "runtime_mode" not in before_columns
    assert "runtime_mode_version" not in before_columns

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        after_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)").fetchall()
        }
    assert {"runtime_mode", "runtime_mode_version"}.issubset(after_columns)

    app = create_app(_settings(database_url, PortalContour.TARGET))
    with TestClient(app):
        assert _metadata(app) == {
            "runtime.portal_mode": "TARGET",
            "runtime.portal_mode_version": "1",
        }

    restarted = create_app(_settings(database_url, PortalContour.SOURCE))
    with TestClient(restarted):
        assert _metadata(restarted) == {
            "runtime.portal_mode": "TARGET",
            "runtime.portal_mode_version": "1",
        }
        with restarted.state.session_factory() as session:
            snapshot = RuntimeModeService(session, restarted.state.settings).current_snapshot()
        assert snapshot.mode is PortalContour.TARGET
        assert snapshot.version == 1


def test_database_backup_restore_preserves_runtime_mode_and_revision(tmp_path: Path) -> None:
    live_path = tmp_path / "live.db"
    live_url = f"sqlite:///{live_path}"
    command.upgrade(_alembic(live_url), "head")
    live_app = create_app(_settings(live_url, PortalContour.SOURCE))
    _seed_operator(live_app)

    with TestClient(live_app) as client:
        headers = _login(client)
        switched = client.put(
            "/api/runtime/mode",
            json={"mode": "TARGET"},
            headers=headers,
        )
        assert switched.status_code == 200
        assert switched.json()["current"] == "TARGET"
        with live_app.state.session_factory() as session:
            live_snapshot = RuntimeModeService(session, live_app.state.settings).current_snapshot()
        assert live_snapshot.mode is PortalContour.TARGET
        assert live_snapshot.version == 2

    live_app.state.db_engine.dispose()
    restored_path = tmp_path / "restored.db"
    shutil.copy2(live_path, restored_path)
    restored_url = f"sqlite:///{restored_path}"
    restored_app = create_app(_settings(restored_url, PortalContour.SOURCE))

    with TestClient(restored_app) as client:
        headers = _login(client)
        assert client.get("/api/runtime", headers=headers).json()["mode"] == "TARGET"
        with restored_app.state.session_factory() as session:
            restored_snapshot = RuntimeModeService(
                session,
                restored_app.state.settings,
            ).current_snapshot()
        assert restored_snapshot.mode is PortalContour.TARGET
        assert restored_snapshot.version == 2

        switched_back = client.put(
            "/api/runtime/mode",
            json={"mode": "SOURCE"},
            headers=headers,
        )
        assert switched_back.status_code == 200
        with restored_app.state.session_factory() as session:
            final_snapshot = RuntimeModeService(
                session,
                restored_app.state.settings,
            ).current_snapshot()
        assert final_snapshot.mode is PortalContour.SOURCE
        assert final_snapshot.version == 3


def test_concurrent_switches_serialize_without_split_brain(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'switches.db'}"
    command.upgrade(_alembic(database_url), "head")
    app = create_app(_settings(database_url, PortalContour.SOURCE))
    _seed_operator(app)

    with TestClient(app):
        with app.state.session_factory() as session:
            actor = UserRepository(session).get_by_username("operator")
            assert actor is not None
            actor_id = actor.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_switch, app, actor_id, PortalContour.TARGET)
                for _ in range(2)
            ]
            results = [future.result(timeout=5) for future in futures]

        assert sorted(result.changed for result in results) == [False, True]
        assert {result.current for result in results} == {PortalContour.TARGET}
        assert {result.mode_version for result in results} == {2}

        with app.state.session_factory() as session:
            snapshot = RuntimeModeService(session, app.state.settings).current_snapshot()
            events = list(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "runtime_mode_changed"
                    )
                )
            )
        assert snapshot.mode is PortalContour.TARGET
        assert snapshot.version == 2
        assert len(events) == 1
        assert json.loads(events[0].metadata_json) == {
            "current": "TARGET",
            "mode_version": 2,
            "previous": "SOURCE",
        }


def test_one_portal_cycles_source_target_source_without_restart(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'cycle.db'}"
    command.upgrade(_alembic(database_url), "head")
    app = create_app(_settings(database_url, PortalContour.SOURCE))
    _seed_operator(app)

    with TestClient(app) as client:
        headers = _login(client)
        assert client.get("/api/runtime", headers=headers).json()["mode"] == "SOURCE"

        import_in_source = client.post("/api/imports/discover", headers=headers)
        assert import_in_source.status_code == 409
        assert import_in_source.json()["error"]["code"] == "import_wrong_contour"

        export_in_source = client.post(
            "/api/exports/preview",
            headers=headers,
            json=_export_selection(),
        )
        assert export_in_source.status_code == 503
        assert export_in_source.json()["error"]["code"] == "harbor_not_configured"

        to_target = client.put(
            "/api/runtime/mode",
            headers=headers,
            json={"mode": "TARGET"},
        )
        assert to_target.status_code == 200
        assert client.get("/api/runtime", headers=headers).json()["mode"] == "TARGET"

        export_in_target = client.post(
            "/api/exports/preview",
            headers=headers,
            json=_export_selection(),
        )
        assert export_in_target.status_code == 409
        assert export_in_target.json()["error"]["code"] == "export_wrong_contour"

        import_in_target = client.post("/api/imports/discover", headers=headers)
        assert import_in_target.status_code == 202

        to_source = client.put(
            "/api/runtime/mode",
            headers=headers,
            json={"mode": "SOURCE"},
        )
        assert to_source.status_code == 200
        assert client.get("/api/runtime", headers=headers).json()["mode"] == "SOURCE"

        import_again = client.post("/api/imports/discover", headers=headers)
        assert import_again.status_code == 409
        assert import_again.json()["error"]["code"] == "import_wrong_contour"

        with app.state.session_factory() as session:
            snapshot = RuntimeModeService(session, app.state.settings).current_snapshot()
            mode_events = list(
                session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.event_type == "runtime_mode_changed")
                    .order_by(AuditEvent.id)
                )
            )
        assert snapshot.mode is PortalContour.SOURCE
        assert snapshot.version == 3
        assert [json.loads(event.metadata_json)["current"] for event in mode_events] == [
            "TARGET",
            "SOURCE",
        ]
