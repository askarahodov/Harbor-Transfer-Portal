from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.config import Settings, get_settings
from app.db.session import create_db_engine, create_session_factory
from app.services.correlated_operation_manager import CorrelatedOperationManager
from app.services.export_recovery import reconcile_incomplete_export_publications
from app.services.operation_audit import install_operation_audit_hooks
from app.utils.errors import http_exception_handler, validation_exception_handler
from app.utils.logging import RequestCorrelationMiddleware, configure_application_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_application_logging(
        level=resolved_settings.log_level,
        json_output=resolved_settings.log_json,
    )
    install_operation_audit_hooks()
    db_engine = create_db_engine(resolved_settings.database_url)
    session_factory = create_session_factory(db_engine)
    operation_manager = CorrelatedOperationManager(session_factory, resolved_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        reconcile_incomplete_export_publications(session_factory, resolved_settings)
        await operation_manager.startup()
        try:
            yield
        finally:
            await operation_manager.shutdown()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description="Air-gap artifact transfer portal API",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.db_engine = db_engine
    app.state.session_factory = session_factory
    app.state.operation_manager = operation_manager

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    if resolved_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )

    app.add_middleware(RequestCorrelationMiddleware)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
