from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.config import PortalContour, validate_harbor_base_url


class HarborSettingsResponse(BaseModel):
    contour: PortalContour
    url: str | None = None
    username: str | None = None
    verify_tls: bool
    credential_configured: bool
    custom_ca_configured: bool
    custom_ca_source: str | None = None


class HarborSettingsUpdateRequest(BaseModel):
    url: AnyHttpUrl | None = None
    username: str | None = Field(default=None, max_length=256)
    verify_tls: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        return validate_harbor_base_url(value)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("username must not be empty")
        return normalized


class HarborCredentialUpdateRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=4096)


class HarborCaUpdateRequest(BaseModel):
    certificate_pem: str = Field(min_length=1, max_length=262_144)


class HarborConnectionTestResponse(BaseModel):
    connected: bool
    version: str | None = None
    auth_mode: str | None = None
