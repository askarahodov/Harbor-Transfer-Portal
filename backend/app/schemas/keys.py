from pydantic import BaseModel, ConfigDict, Field

from app.config import PortalContour


class SigningKeyStatusResponse(BaseModel):
    configured: bool
    fingerprint: str | None = None


class TrustedKeyStatusResponse(BaseModel):
    fingerprint: str
    enabled: bool


class KeySettingsResponse(BaseModel):
    contour: PortalContour
    signing_key: SigningKeyStatusResponse | None = None
    trusted_keys: list[TrustedKeyStatusResponse] = Field(default_factory=list)


class KeyMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pem: str = Field(min_length=1, max_length=65_536)


class TrustedKeyStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class KeyMutationResponse(BaseModel):
    action: str
    fingerprint: str
