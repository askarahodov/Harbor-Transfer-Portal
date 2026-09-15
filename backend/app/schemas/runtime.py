from pydantic import BaseModel

from app.config import PortalContour


class RuntimeModeResponse(BaseModel):
    mode: PortalContour
    version: str


class RuntimeModeUpdateRequest(BaseModel):
    mode: PortalContour


class RuntimeModeUpdateResponse(BaseModel):
    previous: PortalContour
    current: PortalContour
    changed: bool
