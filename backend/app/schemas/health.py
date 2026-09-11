from typing import Literal

from pydantic import BaseModel

from app.config import PortalContour


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    contour: PortalContour


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    checks: dict[str, Literal["ok"]]
