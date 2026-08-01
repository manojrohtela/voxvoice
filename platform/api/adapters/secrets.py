"""Provider credential + secret management seam (M8.18 hardening) — additive.

The **only** place provider credentials are resolved. Infrastructure/provider secrets never live in
code, logs, or VCS (§A11.1.6 / ADR-0022 Security Model); they are injected through a
`CredentialProvider` seam and redacted at every logging/serialisation boundary. This mirrors the
transport seam: a deterministic `EnvCredentialProvider` (reads process env, offline) is the default;
a deployment can inject a vault-backed provider behind the same contract, and a key-rotation seam
lets a credential be resolved through primary → fallback sources without a code change.

No new domain, no new public API — a composition-root seam the adapters bind to.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

# Substrings marking a key as sensitive — redacted wherever structures are logged/serialised.
_SECRET_MARKERS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "password",
    "authorization",
    "credential",
    "private_key",
    "access_key",
)
_REDACTED = "***redacted***"


class CredentialError(Exception):
    """A required credential could not be resolved from any configured source."""


class CredentialProvider(ABC):
    """Resolves a named credential (e.g. ``openai_api_key``). Implementations are injected at the
    composition root; the adapters never read env/vault directly."""

    @abstractmethod
    def get(self, name: str) -> str | None: ...

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise CredentialError(f"required credential {name!r} is not configured")
        return value


class EnvCredentialProvider(CredentialProvider):
    """Default provider: reads `VOXVOICE_SECRET_<NAME>` from the process environment (uppercased).
    Deterministic + offline; a production deployment can inject a vault-backed provider instead."""

    def __init__(
        self, *, prefix: str = "VOXVOICE_SECRET_", overrides: dict[str, str] | None = None
    ):
        self._prefix = prefix
        self._overrides = dict(overrides or {})

    def get(self, name: str) -> str | None:
        if name in self._overrides:
            return self._overrides[name]
        return os.environ.get(self._prefix + name.upper())


class ChainedCredentialProvider(CredentialProvider):
    """Resolves through an ordered list of providers (primary → fallback) — the **key-rotation**
    seam: during a rotation both the new and old sources are configured, and the first that has the
    credential wins. No code change to rotate."""

    def __init__(self, *providers: CredentialProvider) -> None:
        self._providers = providers

    def get(self, name: str) -> str | None:
        for provider in self._providers:
            value = provider.get(name)
            if value:
                return value
        return None


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(marker in k for marker in _SECRET_MARKERS)


def redact(value):
    """Recursively redact secret-looking fields from a dict/list/str for safe logging/serialisation.
    Pure + deterministic; never mutates the input (returns a redacted copy)."""
    if isinstance(value, dict):
        return {k: (_REDACTED if _is_secret_key(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    return value


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact Authorization/API-key headers before logging a provider request."""
    return {k: (_REDACTED if _is_secret_key(k) else v) for k, v in headers.items()}
