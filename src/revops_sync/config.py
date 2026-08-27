from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./revops_sync.db"
    auto_create_schema: bool = True
    workflow_api_key: SecretStr | None = None
    fixture_path: str = "data/synthetic/crm_accounts.json"

    live_integrations_enabled: bool = False
    hubspot_live_enabled: bool = False
    hubspot_access_token: SecretStr | None = None
    hubspot_base_url: str = "https://api.hubapi.com"
    salesforce_live_enabled: bool = False
    salesforce_access_token: SecretStr | None = None
    salesforce_instance_url: str | None = None
    salesforce_api_version: str = "v65.0"
    connector_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    connector_max_retries: int = Field(default=4, ge=0, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
