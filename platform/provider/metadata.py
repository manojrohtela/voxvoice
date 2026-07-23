"""MetadataNormalizer (M5.6B-1) — normalized, future-proofed metadata. No intelligence.

Builds `ProviderMetadata` from the resolved descriptor + timing observations (§12 of the
M5.6A design). `retries` defaults to 0 in the core (no `RetryController` yet — M5.6B-2);
`pricing_metadata`/`extra` are additive escape hatches, never populated with vendor secrets
(no credentials, no raw endpoint with embedded auth).
"""

from __future__ import annotations

from .domain import ProviderMetadata


class MetadataNormalizer:
    def normalize(
        self,
        *,
        provider: str,
        model: str,
        request_id: str,
        response_id: str,
        latency_ms: float = 0.0,
        duration_ms: float = 0.0,
        model_version: str = "",
        region: str | None = None,
        organization: str | None = None,
        retries: int = 0,
        endpoint: str | None = None,
    ) -> ProviderMetadata:
        return ProviderMetadata(
            provider=provider, model=model, model_version=model_version,
            latency_ms=latency_ms, duration_ms=duration_ms,
            request_id=request_id, response_id=response_id,
            region=region, organization=organization, retries=retries, endpoint=endpoint,
        )
