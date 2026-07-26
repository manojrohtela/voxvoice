"""MCPAuthentication (M6.8B-2) — client-side auth material, secrets-seam only.

Produces the credential a transport attaches to the `initialize` handshake and
subsequent requests, for each supported mode (bearer token, OAuth2, mTLS). It
**never reads a secret directly** — every value comes from the injected
`CredentialProvider`; a missing credential is a typed `MCPAuthenticationError`.
Deterministic and offline: OAuth2 uses a provider-supplied access token (the live
token exchange is a real-transport/production concern, not owned here).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .credentials import CredentialProvider
from .domain import MCPServerManifest
from .errors import MCPAuthenticationError


@dataclass(frozen=True, slots=True)
class MCPCredential:
    """The resolved auth material for a session. `headers` attach to requests;
    `tls_cert`/`tls_key` are file references for mTLS. Never contains a raw
    secret in logs/replay — the values are held only for the live transport."""

    mode: str
    headers: Mapping[str, str] = field(default_factory=dict)
    tls_cert: str = ""
    tls_key: str = ""

    def __repr__(self) -> str:
        """Redacted — a credential must never surface a secret in logs, tracebacks,
        replay, or observability. Only the mode and *which* fields are set show."""
        parts = [f"mode={self.mode!r}"]
        if self.headers:
            parts.append(f"headers=<{len(self.headers)} redacted>")
        if self.tls_cert or self.tls_key:
            parts.append("mtls=<redacted>")
        return f"MCPCredential({', '.join(parts)})"


class MCPAuthentication:
    """Resolves an `MCPCredential` for a server manifest through the injected
    `CredentialProvider`. Supported modes: `none`, `token`, `oauth2`, `mtls`."""

    def __init__(self, provider: CredentialProvider | None = None) -> None:
        self._provider = provider

    def authenticate(self, manifest: MCPServerManifest) -> MCPCredential:
        mode = manifest.auth_mode
        if mode == "none":
            return MCPCredential(mode="none")
        if self._provider is None:
            raise MCPAuthenticationError(
                f"server {manifest.qualified_name!r} requires auth_mode {mode!r} "
                "but no CredentialProvider was injected"
            )
        if mode == "token":
            token = self._resolve(manifest, "token_key", f"{manifest.qualified_name}.token")
            return MCPCredential(mode="token", headers={"Authorization": f"Bearer {token}"})
        if mode == "oauth2":
            token = self._resolve(
                manifest, "oauth2_token_key", f"{manifest.qualified_name}.oauth2.access_token"
            )
            return MCPCredential(mode="oauth2", headers={"Authorization": f"Bearer {token}"})
        if mode == "mtls":
            cert = self._resolve(manifest, "mtls_cert_key", f"{manifest.qualified_name}.mtls.cert")
            key = self._resolve(manifest, "mtls_key_key", f"{manifest.qualified_name}.mtls.key")
            return MCPCredential(mode="mtls", tls_cert=cert, tls_key=key)
        raise MCPAuthenticationError(f"unknown auth_mode {mode!r}")

    def _resolve(self, manifest: MCPServerManifest, metadata_key: str, default_key: str) -> str:
        key = manifest.metadata.get(metadata_key, default_key)
        try:
            return self._provider.resolve(key)  # type: ignore[union-attr]
        except KeyError as exc:
            raise MCPAuthenticationError(
                f"missing credential {key!r} for server {manifest.qualified_name!r}"
            ) from exc
