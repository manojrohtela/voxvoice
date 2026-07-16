"""Application settings, all sourced from the environment (never hardcoded)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"

    # Engine selection is config-driven (SPEC §7 Phase 1 acceptance).
    engine: str = "dograh"  # "dograh" | "livekit"

    database_url: str = "sqlite:///./voxvoice.db"

    # Dograh self-hosted instance (VERIFIED v1.35.0).
    # REST API host root — `/api/v1` is appended by the adapter. The official
    # compose maps the api service to host port 8000; this repo verified against
    # a remapped 8010 (8000 was already in use). The UI/embed host is :3010.
    dograh_base_url: str = "http://localhost:8000"
    dograh_ui_base_url: str = "http://localhost:3010"
    dograh_api_key: str | None = None  # `dgr_…` from Dograh's /user/api-keys

    # Secret the engine must present when calling our event-ingestion endpoint.
    engine_callback_secret: str = "change-me-engine-callback-secret"

    # Fallback webhook config (used only when an Org has none of its own).
    default_webhook_url: str | None = None
    default_webhook_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
