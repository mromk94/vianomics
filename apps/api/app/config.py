from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "VAIIP API"
    version: str = "0.1.0"

    database_url: str = "postgresql://vianomics:vianomics@localhost:5432/vianomics"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"

    # Serve explicitly-labeled demo fixtures for unpopulated sections.
    # Must be false in any environment connected to a real account.
    demo_fixtures: bool = True

    # Hard gate: no order path may execute unless this is enabled AND
    # risk + human approvals exist. Default off.
    execution_enabled: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
