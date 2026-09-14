from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_PATTERN = re.compile(
    r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]+)",
)
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
    r"(?![A-Za-z0-9_-])"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)((?:^|[\s,{;])[\"']?[A-Za-z0-9_.-]*"
    r"(?:password|passwd|token|secret|jwt|authorization|private[_-]?key)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_MAX_LOGGED_PATH_LENGTH = 512
_APPLICATION_HANDLER_MARKER = "_htp_application_handler"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_operation_id: ContextVar[int | None] = ContextVar("operation_id", default=None)


def normalize_request_id(value: str | None) -> str:
    if value is not None:
        candidate = value.strip()
        if _REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return uuid4().hex


def current_request_id() -> str | None:
    return _request_id.get()


def current_operation_id() -> int | None:
    return _operation_id.get()


@contextmanager
def operation_log_context(operation_id: int) -> Iterator[None]:
    token = _operation_id.set(operation_id)
    try:
        yield
    finally:
        _operation_id.reset(token)


def redact_log_text(value: str) -> str:
    redacted = _PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", value)
    redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _JWT_PATTERN.sub("[REDACTED_JWT]", redacted)
    return _SECRET_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", redacted)


def _bounded_path(scope: Scope) -> str:
    path = str(scope.get("path", "-"))
    if len(path) <= _MAX_LOGGED_PATH_LENGTH:
        return path
    return f"{path[: _MAX_LOGGED_PATH_LENGTH - 3]}..."


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or "-"
        operation_id = current_operation_id()
        if operation_id is None:
            explicit = getattr(record, "operation_id", None)
            operation_id = explicit if isinstance(explicit, int) else None
        record.operation_id = operation_id if operation_id is not None else "-"
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "message": redact_log_text(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
            "operation_id": getattr(record, "operation_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = redact_log_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_application_logging(*, level: str, json_output: bool) -> None:
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)

    # Replace only the handler owned by this application. External handlers
    # (pytest caplog, observability agents, embedding applications) must remain intact.
    for existing in list(app_logger.handlers):
        if getattr(existing, _APPLICATION_HANDLER_MARKER, False):
            app_logger.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler()
    setattr(handler, _APPLICATION_HANDLER_MARKER, True)
    handler.addFilter(CorrelationFilter())
    if json_output:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            RedactingFormatter(
                "%(asctime)s %(levelname)s %(name)s "
                "request_id=%(request_id)s operation_id=%(operation_id)s %(message)s"
            )
        )
    app_logger.addHandler(handler)

    # Keep normal logging propagation semantics so test/host/root handlers can
    # observe application records. The dedicated application handler remains the
    # source of the portal's formatted stdout/stderr stream.
    app_logger.propagate = True


class RequestCorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = logging.getLogger("app.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = normalize_request_id(headers.get("x-request-id"))
        request_token = _request_id.set(request_id)
        started = time.monotonic()
        status_code = 500
        response_started = False
        path = _bounded_path(scope)

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            if response_started:
                raise
            self.logger.exception(
                "unhandled request exception method=%s path=%s",
                scope.get("method", "-"),
                path,
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "Internal server error",
                    }
                },
            )
            await response(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            self.logger.info(
                "request completed method=%s path=%s status_code=%s duration_ms=%.1f",
                scope.get("method", "-"),
                path,
                status_code,
                duration_ms,
            )
            _request_id.reset(request_token)
