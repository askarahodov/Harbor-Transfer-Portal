from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, field_validator, model_validator

from app.config import PortalContour, validate_harbor_base_url


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


class TransferSettingsResponse(BaseModel):
    import_allow_overwrite: bool
    import_max_upload_bytes: int
    bundle_max_archive_bytes: int
    bundle_max_extracted_bytes: int
    bundle_max_member_count: int
    operation_max_concurrent: int
    operation_max_concurrent_active: int
    restart_required_fields: list[str] = Field(default_factory=list)


class TransferSettingsPatch(BaseModel):
    import_allow_overwrite: bool | None = None
    import_max_upload_bytes: int | None = Field(default=None, ge=1024**2, le=1024**4)
    bundle_max_archive_bytes: int | None = Field(default=None, ge=1024**2, le=1024**4)
    bundle_max_extracted_bytes: int | None = Field(
        default=None,
        ge=1024**2,
        le=2 * 1024**4,
    )
    bundle_max_member_count: int | None = Field(default=None, ge=4, le=1_000_000)
    operation_max_concurrent: int | None = Field(default=None, ge=1, le=32)

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "TransferSettingsPatch":
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self
