from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.runtime_mode import RuntimeModeError


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _structured_detail(detail: Any) -> tuple[str, str] | None:
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    message = detail.get("message")
    if isinstance(code, str) and isinstance(message, str):
        return code, message
    return None


async def http_exception_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    structured = _structured_detail(exc.detail)
    if structured is not None:
        code, message = structured
        return _response(exc.status_code, code, message)

    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _response(exc.status_code, f"http_{exc.status_code}", detail)


async def validation_exception_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return _response(422, "validation_error", "Request validation failed")


async def runtime_mode_exception_handler(
    _request: Request, exc: RuntimeModeError
) -> JSONResponse:
    status_code = 500 if exc.code == "runtime_mode_invalid" else 409
    return _response(status_code, exc.code, exc.message)
