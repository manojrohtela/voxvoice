"""Application settings, all sourced from the environment (never hardcoded).

The engine-adapter settings (ENGINE, DOGRAH_*, ENGINE_CALLBACK_SECRET) were
removed at M1 with the adapter axis (ADR-0001/0007). Runtime and provider
configuration is introduced with the Runtime Plane (M2+), per agent, and
validated at write time (Constitution §A2.6 — no dead config, AR-25).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"

    # Database. SQLite locally; a Postgres/Supabase URL in prod with NO model
    # changes (Constitution Part B / ADR-0006), e.g.
    # postgresql+psycopg://USER:PASS@HOST:5432/postgres
    database_url: str = "sqlite:///./voxvoice.db"

    # Fallback webhook config (used only when an Org has none of its own).
    default_webhook_url: str | None = None
    default_webhook_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
