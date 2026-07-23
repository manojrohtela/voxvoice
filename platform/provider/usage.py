"""UsageNormalizer (M5.6B-1) — normalize usage counts. No billing.

Turns raw prompt/completion text into a normalized `ProviderUsage` via an injected token
counter (a deterministic default — whitespace word count, no vendor tokenizer in the core).
Cached/reasoning/image/audio/embedding counts default to zero here; real adapters populate
them from vendor-reported usage once they land (additive — the shape already carries the
fields, per §13 of the M5.6A design). **Never computes a price** — that is a separate
downstream metering concern (`EventType.COST_UPDATED`).
"""

from __future__ import annotations

from collections.abc import Callable

from .domain import ProviderUsage


class UsageNormalizer:
    def __init__(self, *, token_counter: Callable[[str], int] | None = None) -> None:
        self._count = token_counter or (lambda s: len(s.split()))

    def normalize(
        self,
        *,
        prompt_text: str,
        completion_text: str,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        image_units: int = 0,
        audio_seconds: float = 0.0,
        embedding_tokens: int = 0,
    ) -> ProviderUsage:
        return ProviderUsage(
            prompt_tokens=self._count(prompt_text),
            completion_tokens=self._count(completion_text),
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            image_units=image_units,
            audio_seconds=audio_seconds,
            embedding_tokens=embedding_tokens,
        )
