from fastapi import APIRouter

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.exports import router as exports_router
from app.api.harbor import router as harbor_router
from app.api.health import router as health_router
from app.api.imports import router as imports_router
from app.api.key_settings import router as key_settings_router
from app.api.operations import router as operations_router
from app.api.runtime import router as runtime_router
from app.api.settings import router as settings_router
from app.api.transfer_settings import router as transfer_settings_router
from app.api.users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(harbor_router)
api_router.include_router(settings_router)
api_router.include_router(transfer_settings_router)
api_router.include_router(key_settings_router)
api_router.include_router(operations_router)
api_router.include_router(audit_router)
api_router.include_router(runtime_router)
api_router.include_router(exports_router)
api_router.include_router(imports_router)
