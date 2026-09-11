from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


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


def get_settings() -> Settings:
    return Settings()
