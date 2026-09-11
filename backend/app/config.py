from enum import StrEnum

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalContour(StrEnum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    app_name: str = "Harbor Transfer Portal"
    app_version: str = "0.1.0"
    portal_contour: PortalContour = PortalContour.SOURCE

    harbor_url: AnyHttpUrl | None = None
    harbor_user: str | None = None
    harbor_password: SecretStr | None = None

    cors_origins: list[str] = []


def get_settings() -> Settings:
    return Settings()
