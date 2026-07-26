"""MCP transport trust model (M6.8.1, F5) — explicit, not library-default.

A frozen `MCPTrustPolicy` states, for the *real* (non-loopback) transports, which
endpoints may be reached and how TLS must be established: an endpoint allow-list,
an explicit TLS-verification stance, an optional CA bundle, and an mTLS
requirement. The policy is **optional** — when absent (the offline/loopback
default) nothing is enforced and behaviour is unchanged; when present it is
enforced *fail-closed* (an empty allow-list denies every real endpoint).

Determinism: policy checks are pure string/URL comparisons. The SSL context is
built from the policy + resolved credential only — MCP still never reads a secret
itself (the client cert/key file references arrive via `MCPCredential`, resolved
through the injected `CredentialProvider`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .domain import MCPTransportKind
from .errors import MCPTransportError

_HTTP_KINDS = frozenset({MCPTransportKind.HTTP_SSE, MCPTransportKind.STREAMABLE_HTTP})


@dataclass(frozen=True, slots=True)
class MCPTrustPolicy:
    """Explicit transport trust for real MCP servers.

    - `allowed_endpoints`: exact URLs (http) or command names (stdio) permitted;
      matched by prefix. Empty + `enforce_allow_list` ⇒ deny all (fail-closed).
    - `verify_tls`: verify the server certificate + hostname (default on).
    - `ca_bundle`: optional CA file used *instead of* the system default trust store.
    - `require_mtls`: an https endpoint must present a client cert (from the
      resolved `MCPCredential`), else the connection is refused.
    - `allow_insecure_http`: permit plain `http://` (default: https only).
    """

    allowed_endpoints: frozenset[str] = field(default_factory=frozenset)
    verify_tls: bool = True
    ca_bundle: str = ""
    require_mtls: bool = False
    allow_insecure_http: bool = False
    enforce_allow_list: bool = True

    # ── endpoint admission ──────────────────────────────────────────────────────
    def check_endpoint(self, kind: MCPTransportKind, endpoint: str) -> None:
        """Admit or reject a transport endpoint. Raises `MCPTransportError`
        (never a bare exception) when the endpoint is not trusted."""
        if kind in _HTTP_KINDS:
            scheme = urlparse(endpoint).scheme.lower()
            if scheme == "http" and not self.allow_insecure_http:
                raise MCPTransportError(
                    f"insecure http endpoint refused by trust policy: {endpoint!r} "
                    "(set allow_insecure_http to permit)"
                )
            if scheme not in {"http", "https"}:
                raise MCPTransportError(f"unsupported endpoint scheme for {endpoint!r}")
        self._check_allow_list(endpoint)

    def _check_allow_list(self, endpoint: str) -> None:
        if not self.enforce_allow_list:
            return
        if not any(endpoint.startswith(prefix) for prefix in self.allowed_endpoints):
            raise MCPTransportError(
                f"endpoint {endpoint!r} is not in the trust allow-list "
                f"({len(self.allowed_endpoints)} permitted prefix(es))"
            )

    # ── TLS ─────────────────────────────────────────────────────────────────────
    def ssl_context(self, *, tls_cert: str = "", tls_key: str = ""):
        """Build an explicit `ssl.SSLContext` for an https endpoint from this
        policy and any resolved mTLS material. Never relies solely on library
        defaults: verification mode and hostname checking are set explicitly."""
        import ssl

        context = ssl.create_default_context(cafile=self.ca_bundle or None)
        if self.verify_tls:
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if tls_cert and tls_key:
            context.load_cert_chain(certfile=tls_cert, keyfile=tls_key)
        elif self.require_mtls:
            raise MCPTransportError(
                "trust policy requires mTLS but no client certificate was resolved"
            )
        return context
