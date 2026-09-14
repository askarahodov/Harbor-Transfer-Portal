from pydantic import BaseModel, Field, SecretStr, field_validator

from app.config import PortalContour

_MAX_KEY_MATERIAL_CHARS = 16 * 1024


class SigningIdentityResponse(BaseModel):
    configured: bool
    key_id: str | None = None
    fingerprint: str | None = None


class TrustedKeyResponse(BaseModel):
    key_id: str
    fingerprint: str
    enabled: bool


class KeyManagementResponse(BaseModel):
    contour: PortalContour
    signing: SigningIdentityResponse | None = None
    trusted_keys: list[TrustedKeyResponse] = Field(default_factory=list)


class SigningKeyInstallRequest(BaseModel):
    private_key_pem: SecretStr
    confirm_rotation: bool = False

    @field_validator("private_key_pem")
    @classmethod
    def validate_private_key_size(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw or len(raw.encode("utf-8")) > _MAX_KEY_MATERIAL_CHARS:
            raise ValueError("private key material size is invalid")
        return value


class TrustedKeyInstallRequest(BaseModel):
    public_key_pem: str = Field(min_length=1, max_length=_MAX_KEY_MATERIAL_CHARS)
    confirm: bool = False


class TrustedKeyStateRequest(BaseModel):
    enabled: bool
    confirm: bool = False
