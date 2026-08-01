"""Control Plane API configuration (M8.5).

Environment-driven settings for the Customer Platform HTTP API. Frozen value object;
loaded from `VOXVOICE_API_*` environment variables with safe development defaults, and
validated at startup (a production environment may not run on the default JWT secret —
fail fast, §A2.6). No business logic here — transport/config only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from core.errors import ConfigError

_DEFAULT_SECRET = "dev-secret-change-me"


@dataclass(frozen=True, slots=True)
class CustomerApiSettings:
    environment: str = "development"  # development | testing | production
    database_url: str = "sqlite://"
    jwt_secret: str = _DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    token_ttl_seconds: int = 3600
    cors_origins: tuple[str, ...] = ("*",)
    api_title: str = "VoxVoice Control Plane API"
    api_version: str = "1.0.0"
    # M8.18 hardening — transport limits. `max_request_bytes` caps the request body (413 over it);
    # `rate_limit_per_minute` throttles per client (429; 0 disables — the test/dev default).
    max_request_bytes: int = 4_000_000
    rate_limit_per_minute: int = 0
    # M8.18 — JWT key-rotation seam: additional accepted verification secrets (old keys still valid
    # during a rotation window). Signing always uses `jwt_secret`; verification tries all.
    jwt_secondary_secrets: tuple[str, ...] = ()

    @classmethod
    def load(cls, **overrides: object) -> CustomerApiSettings:
        env = os.environ
        origins = env.get("VOXVOICE_API_CORS_ORIGINS")
        secondary = env.get("VOXVOICE_API_JWT_SECONDARY_SECRETS")
        base = cls(
            environment=env.get("VOXVOICE_API_ENV", "development"),
            database_url=env.get("VOXVOICE_API_DATABASE_URL", "sqlite://"),
            jwt_secret=env.get("VOXVOICE_API_JWT_SECRET", _DEFAULT_SECRET),
            token_ttl_seconds=int(env.get("VOXVOICE_API_TOKEN_TTL", "3600")),
            cors_origins=tuple(o.strip() for o in origins.split(",")) if origins else ("*",),
            max_request_bytes=int(env.get("VOXVOICE_API_MAX_REQUEST_BYTES", "4000000")),
            rate_limit_per_minute=int(env.get("VOXVOICE_API_RATE_LIMIT_PER_MINUTE", "0")),
            jwt_secondary_secrets=(
                tuple(s.strip() for s in secondary.split(",") if s.strip()) if secondary else ()
            ),
        )
        settings = replace(base, **overrides) if overrides else base
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in ("development", "testing", "production"):
            raise ConfigError(f"invalid environment {self.environment!r}")
        if self.token_ttl_seconds <= 0:
            raise ConfigError("token_ttl_seconds must be positive")
        if self.max_request_bytes <= 0:
            raise ConfigError("max_request_bytes must be positive")
        if self.rate_limit_per_minute < 0:
            raise ConfigError("rate_limit_per_minute must be non-negative")
        if self.is_production:
            # Production fails fast on insecure defaults (§A2.6 — declared, never silent).
            if self.jwt_secret == _DEFAULT_SECRET:
                raise ConfigError(
                    "production API must set VOXVOICE_API_JWT_SECRET (not the default)"
                )
            if "*" in self.cors_origins:
                raise ConfigError("production API must not allow wildcard CORS origins")
            if self.database_url in ("sqlite://", "sqlite:///:memory:"):
                raise ConfigError(
                    "production API must use a durable database, not in-memory sqlite"
                )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def verification_secrets(self) -> tuple[str, ...]:
        """Secrets a token may be verified against (primary + rotated) — the rotation seam."""
        return (self.jwt_secret, *self.jwt_secondary_secrets)


# Metadata for OpenAPI tag grouping (transport concern).
OPENAPI_TAGS: list[dict] = [
    {"name": "auth", "description": "Registration, dev token issuance, and identity."},
    {"name": "organizations", "description": "Organization lifecycle."},
    {"name": "workspaces", "description": "Workspaces and their environments."},
    {"name": "members", "description": "Organization membership and role grants."},
    {"name": "projects", "description": "Projects within a workspace."},
    {"name": "environments", "description": "Deployment target profiles within a workspace."},
    {"name": "preferences", "description": "Per-user Studio preferences (Consumer-owned)."},
    {"name": "system", "description": "Health, readiness, and version."},
]
