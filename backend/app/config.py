from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
JWT_SECRET_PLACEHOLDER = "replace-with-random-high-entropy-secret"


class PortalContour(StrEnum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"


def validate_harbor_base_url(value: AnyHttpUrl | None) -> AnyHttpUrl | None:
    if value is None:
        return None
    if value.username is not None or value.password is not None:
        raise ValueError("HARBOR_URL must not contain credentials")
    if value.query is not None or value.fragment is not None:
        raise ValueError("HARBOR_URL must not contain query or fragment")
    if value.path not in (None, "", "/"):
        raise ValueError("HARBOR_URL must point to the Harbor origin without a subpath")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Harbor Transfer Portal"
    app_version: str = "0.1.0"
    portal_contour: PortalContour = PortalContour.SOURCE
    database_url: str = "sqlite:///./data/harbor-transfer-portal.db"

    harbor_url: AnyHttpUrl | None = None
    harbor_user: str | None = None
    harbor_password: SecretStr | None = None
    harbor_password_file: Path | None = None
    harbor_runtime_password_file: Path = Path("/app/data/secrets/harbor-password")
    harbor_verify_tls: bool = True
    harbor_ca_file: Path | None = None
    harbor_runtime_ca_file: Path = Path("/app/data/secrets/harbor-ca.crt")
    harbor_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    harbor_read_timeout_seconds: float = Field(default=20.0, gt=0, le=300)

    jwt_secret: SecretStr | None = None
    jwt_access_token_minutes: int = Field(default=30, ge=1, le=1440)
    login_rate_limit_window_seconds: int = Field(default=300, ge=1, le=86400)
    login_rate_limit_username_max_failures: int = Field(default=5, ge=1, le=100)
    login_rate_limit_address_max_failures: int = Field(default=20, ge=1, le=1000)
    login_rate_limit_lockout_seconds: int = Field(default=900, ge=1, le=86400)

    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("harbor_url")
    @classmethod
    def validate_harbor_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        return validate_harbor_base_url(value)

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        if len(secret) < 32 or secret == JWT_SECRET_PLACEHOLDER:
            raise ValueError("JWT_SECRET must be a unique secret of at least 32 characters")
        return SecretStr(secret)


def get_settings() -> Settings:
    return Settings()
