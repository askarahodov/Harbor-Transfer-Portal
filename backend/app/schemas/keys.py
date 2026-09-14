from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import PortalContour


class SourceSigningKeyStatusResponse(BaseModel):
    configured: bool
    fingerprint: str | None = None


class TrustedPublicKeyResponse(BaseModel):
    fingerprint: str
    enabled: bool


class KeyManagementStatusResponse(BaseModel):
    contour: PortalContour
    source_signing: SourceSigningKeyStatusResponse | None = None
    trusted_keys: list[TrustedPublicKeyResponse] = Field(default_factory=list)


class SourceSigningKeyInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    private_key_pem: str = Field(min_length=1, max_length=65_536)
    confirm: Literal[True]


class TrustedPublicKeyInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key_pem: str = Field(min_length=1, max_length=65_536)
    confirm: Literal[True]


class KeyConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]
