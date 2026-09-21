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
    pending_signing_key: SigningKeyStatusResponse | None = None
    trusted_keys: list[TrustedKeyStatusResponse] = Field(default_factory=list)


class KeyMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pem: str = Field(min_length=1, max_length=1_048_576)


class TrustedKeyMaterialRequest(KeyMaterialRequest):
    confirm: bool = False


class TrustedKeyStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    confirm: bool = False


class KeyMutationResponse(BaseModel):
    action: str
    fingerprint: str


class SigningRotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class TrustedKeyRetirementImpactResponse(BaseModel):
    fingerprint: str
    enabled: bool
    enabled_key_count: int
    historical_import_count: int
    blocking_operation_ids: list[int] = Field(default_factory=list)
    can_retire: bool
