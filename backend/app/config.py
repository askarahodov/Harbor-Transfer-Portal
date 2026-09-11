from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
JWT_SECRET_PLACEHOLDER = "replace-with-random-high-entropy-secret"


class PortalContour(StrEnum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"


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

    jwt_secret: SecretStr | None = None
    jwt_access_token_minutes: int = Field(default=30, ge=1, le=1440)

    cors_origins: list[str] = Field(default_factory=list)

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
