"""Provider observability (M5.6B-1) — metric names + lifecycle counters.

The canonical meter-key constants; `ProviderStatistics` (the read-side snapshot) lives in
`domain`. Routing/health/policy statistics are M5.6B-2; the core surfaces lifecycle counters
+ temporary-storage size + provider names — requests, streaming count, usage, provider
selected, response duration.
"""

from __future__ import annotations


class Metric:
    REQUESTED = "provider.requested"
    ROUTED = "provider.routed"
    EXECUTING = "provider.executing"
    STREAMING = "provider.streaming"
    CHUNKS = "provider.chunks"
    COMPLETED = "provider.completed"
    DELIVERED = "provider.delivered"
    FAILED = "provider.failed"
    EXPIRED = "provider.expired"
    PROMPT_TOKENS = "provider.tokens.prompt"
    COMPLETION_TOKENS = "provider.tokens.completion"
    REQUEST_LATENCY = "provider.request.latency"
    PROVIDER_SELECTED = "provider.selected"
