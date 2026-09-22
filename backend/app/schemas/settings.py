from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, field_validator, model_validator

from app.config import PortalContour, validate_harbor_base_url


class HarborProfileResponse(BaseModel):
    id: str
    name: str
    url: str | None = None
    username: str | None = None
    verify_tls: bool
    enabled: bool
    credential_configured: bool
    custom_ca_configured: bool
    legacy_default: bool = False


class HarborProfileListResponse(BaseModel):
    items: list[HarborProfileResponse]


class HarborProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl
    username: str | None = Field(default=None, max_length=256)
    verify_tls: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        validated = validate_harbor_base_url(value)
        if validated is None:
            raise ValueError("url is required")
        return validated

    @field_validator("name", "username")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class HarborProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: AnyHttpUrl | None = None
    username: str | None = Field(default=None, max_length=256)
    verify_tls: bool | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        return validate_harbor_base_url(value)

    @field_validator("name", "username")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "HarborProfilePatch":
        for field in ("name", "url", "verify_tls", "enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class HarborSettingsResponse(BaseModel):
    contour: PortalContour
    url: str | None = None
    username: str | None = None
    verify_tls: bool
    credential_configured: bool
    custom_ca_configured: bool


class HarborSettingsPatch(BaseModel):
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
        return normalized or None

    @model_validator(mode="after")
    def validate_verify_tls(self) -> "HarborSettingsPatch":
        if "verify_tls" in self.model_fields_set and self.verify_tls is None:
            raise ValueError("verify_tls must be true or false")
        return self


class HarborCredentialRequest(BaseModel):
    secret: SecretStr

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("credential must not be empty")
        return value


class HarborCARequest(BaseModel):
    certificate_pem: str = Field(min_length=1, max_length=262_144)


class HarborMutationResponse(BaseModel):
    changed_fields: list[str]


class HarborConnectionTestResponse(BaseModel):
    ok: bool
    code: str
    message: str
    version: str | None = None
