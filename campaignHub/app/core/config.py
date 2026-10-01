from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://campaignhub:campaignhub_local@localhost:5435/campaignhub"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CAMPAIGNHUB_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "CampaignHub"
    environment: Environment = "development"
    debug: bool = False
    log_level: LogLevel = "INFO"
    api_v1_prefix: str = "/api/v1"

    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)

    @field_validator("app_name", "api_v1_prefix", mode="before")
    @classmethod
    def strip_text_values(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Application name cannot be empty")

        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()

        return value

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("API prefix must begin with '/'")

        if value != "/" and value.endswith("/"):
            raise ValueError("API prefix must not end with '/'")

        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError("Debug mode must be disabled in production")

        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
