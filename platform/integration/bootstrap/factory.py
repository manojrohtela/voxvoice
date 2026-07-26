"""PlatformFactory (M6.7B-1) — the single entry point that assembles the
platform. Mirrors every capability's own `*Factory` composition-root shape.

`default()` builds the fully-offline, deterministic platform (the same profile
`RuntimePipelineFactory.default()` and every capability factory's own
`default()` already establish) — no network, no vendor SDK. `create()` accepts
an `IntegrationConfiguration` for the layer's own tunable knobs (design §3.2).
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import IntegrationConfiguration
from .composition_root import PlatformBuilder, PlatformCompositionRoot


class PlatformFactory:
    @staticmethod
    def create(
        config: IntegrationConfiguration | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> PlatformCompositionRoot:
        return PlatformBuilder(config, clock=clock).build()

    @staticmethod
    def default(*, clock: Callable[[], float] | None = None) -> PlatformCompositionRoot:
        return PlatformFactory.create(IntegrationConfiguration(), clock=clock)
