"""Credential seam (M6.8B-2) — MCP never retrieves secrets directly.

`CredentialProvider` mirrors `tool.extensions.SecretsManager`'s exact shape
(`resolve(key) -> str`) — duplicated, not imported (`mcp → core` only). A
composition root injects a concrete provider; `MCPAuthentication` resolves every
credential through it and never reads env/files/manifests itself. A missing key
is a typed failure, surfaced by the caller as `MCPAuthenticationError`.
`StaticCredentialProvider` is the deterministic offline/test default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class CredentialProvider(ABC):
    """Where MCP auth resolves its credentials — never via a manifest, env, or
    file read inside MCP itself."""

    @abstractmethod
    def resolve(self, key: str) -> str:
        """Return the secret for `key`. Raise `KeyError` if absent (the auth
        layer translates that into a typed `MCPAuthenticationError`)."""


class StaticCredentialProvider(CredentialProvider):
    """A deterministic, in-memory provider for offline validation/tests. Holds no
    real secrets; values are supplied by the test/composition root."""

    def __init__(self, secrets: Mapping[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def resolve(self, key: str) -> str:
        return self._secrets[key]  # KeyError -> MCPAuthenticationError at the auth boundary
