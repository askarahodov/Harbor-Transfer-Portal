from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.harbor import router as harbor_router
from app.api.health import router as health_router
from app.api.settings import router as settings_router
from app.api.users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(harbor_router)
api_router.include_router(settings_router)
