import asyncio
import io
import json
import logging
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError

from alembic import command
from app.config import Settings
from app.main import create_app
from app.utils.logging import (
    CorrelationFilter,
    JsonLogFormatter,
    configure_application_logging,
    current_operation_id,
    operation_log_context,
    redact_log_text,
)


def _migrated_app(tmp_path: Path, *, log_json: bool = True):
    database_url = f"sqlite:///{tmp_path / 'logging.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return create_app(
        Settings(
            _env_file=None,
            database_url=database_url,
            operation_workspace_root=tmp_path / "work",
            operation_disk_reserve_bytes=0,
            log_json=log_json,
        )
    )


def test_request_correlation_preserves_safe_id_and_drops_query_from_log(tmp_path: Path) -> None:
    app = _migrated_app(tmp_path)
    app_logger = logging.getLogger("app")
    stream = io.StringIO()
    assert app_logger.handlers
    app_logger.handlers[0].setStream(stream)

    with TestClient(app) as client:
        response = client.get(
            "/api/health?password=must-not-appear",
            headers={"X-Request-ID": "client-request_42"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "client-request_42"
    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    request_record = next(item for item in records if item["component"] == "app.http")
    assert request_record["request_id"] == "client-request_42"
    assert request_record["operation_id"] == "-"
    assert "/api/health" in request_record["message"]
    assert "must-not-appear" not in request_record["message"]


def test_request_correlation_replaces_unsafe_or_oversized_id(tmp_path: Path) -> None:
    app = _migrated_app(tmp_path, log_json=False)

    with TestClient(app) as client:
        response = client.get(
            "/api/health",
            headers={"X-Request-ID": "x" * 65},
        )

    generated = response.headers["x-request-id"]
    assert generated != "x" * 65
    assert len(generated) == 32
    assert generated.isalnum()


def test_unexpected_500_keeps_request_id_and_hides_exception_secret(tmp_path: Path) -> None:
    app = _migrated_app(tmp_path)
    app_logger = logging.getLogger("app")
    stream = io.StringIO()
    app_logger.handlers[0].setStream(stream)

    @app.get("/_test/unhandled")
    def explode() -> None:
        raise RuntimeError("password=must-never-reach-log")

    with TestClient(app) as client:
        response = client.get(
            "/_test/unhandled",
            headers={"X-Request-ID": "failure-request-1"},
        )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "failure-request-1"
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "must-never-reach-log" not in stream.getvalue()


def test_redaction_removes_bearer_password_token_and_private_key() -> None:
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "super-sensitive-key-material\n"
        "-----END PRIVATE KEY-----"
    )
    rendered = redact_log_text(
        "Authorization: Bearer eyJ.secret.signature "
        "password=hunter2 token=opaque-token "
        f"key={private_key}"
    )
    jsonish = redact_log_text('{"password":"json-secret","token":"json-token"}')

    assert "eyJ.secret.signature" not in rendered
    assert "hunter2" not in rendered
    assert "opaque-token" not in rendered
    assert "super-sensitive-key-material" not in rendered
    assert "json-secret" not in jsonish
    assert "json-token" not in jsonish
    assert "[REDACTED]" in rendered
    assert "[REDACTED]" in jsonish
    assert "[REDACTED_PRIVATE_KEY]" in rendered


def test_json_formatter_emits_stable_safe_fields() -> None:
    record = logging.LogRecord(
        name="app.tests",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="token=%s",
        args=("sensitive-token-value",),
        exc_info=None,
    )
    with operation_log_context(77):
        CorrelationFilter().filter(record)
        payload = json.loads(JsonLogFormatter().format(record))

    assert payload["level"] == "WARNING"
    assert payload["component"] == "app.tests"
    assert payload["operation_id"] == 77
    assert payload["request_id"] == "-"
    assert "sensitive-token-value" not in payload["message"]
    assert payload["message"] == "token=[REDACTED]"
    assert payload["timestamp"].endswith("+00:00")


def test_operation_worker_task_name_seeds_operation_correlation() -> None:
    async def probe() -> int | None:
        task = asyncio.current_task()
        assert task is not None
        task.set_name("operation-314")
        return current_operation_id()

    assert asyncio.run(probe()) == 314


def test_configure_logging_preserves_external_handlers_and_propagation() -> None:
    app_logger = logging.getLogger("app")
    external = logging.NullHandler()
    app_logger.addHandler(external)
    try:
        configure_application_logging(level="INFO", json_output=False)
        configure_application_logging(level="DEBUG", json_output=True)

        assert external in app_logger.handlers
        assert app_logger.propagate is True
        owned = [
            handler
            for handler in app_logger.handlers
            if getattr(handler, "_htp_application_handler", False)
        ]
        assert len(owned) == 1
        assert app_logger.level == logging.DEBUG
    finally:
        app_logger.removeHandler(external)


def test_log_level_is_normalized_and_invalid_value_rejected() -> None:
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="verbose")
