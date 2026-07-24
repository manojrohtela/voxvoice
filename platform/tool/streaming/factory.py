"""StreamingFactory — the composition root for `ToolStreamingRuntime` (M6.1B-4).
Mirrors `tool.factory.ToolFactory`: takes injected `(ToolManifest, StreamingAdapter)`
registrations (the deterministic `EchoStreamingAdapter` by default).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..domain import Classification, ToolManifest
from ..extensions import StreamingAdapter
from ..registry import ToolRegistry
from .adapters import EchoStreamingAdapter
from .controller import ToolStreamingController
from .policies import StreamingPolicy, ToolStreamingPolicyEngine
from .runtime import Publisher, ToolStreamingRuntime


class StreamingFactory:
    @staticmethod
    def create(
        *,
        registry: ToolRegistry | None = None,
        tools: Sequence[tuple[ToolManifest, StreamingAdapter]] = (),
        auto_activate: bool = True,
        policy: StreamingPolicy | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
        **kwargs: Any,
    ) -> ToolStreamingRuntime:
        import time

        the_clock = clock or time.time
        reg = registry or ToolRegistry(clock=the_clock)

        if registry is None:
            registered = [_echo_stream_manifest(), *[m for m, _a in tools]]
            adapters: list[StreamingAdapter] = [EchoStreamingAdapter(), *[a for _m, a in tools]]
            for manifest, adapter in zip(registered, adapters, strict=True):
                reg.register(manifest, adapter)
                if auto_activate:
                    reg.activate(manifest.namespace, manifest.name, manifest.version)

        controller = ToolStreamingController(ToolStreamingPolicyEngine(policy))
        return ToolStreamingRuntime(
            registry=reg, controller=controller, clock=the_clock, publish=publish, **kwargs,
        )


def _echo_stream_manifest() -> ToolManifest:
    return ToolManifest(
        name="echo_stream", namespace="internal", version="1.0.0",
        description="Deterministic offline streaming echo tool (dev/test default).",
        capabilities=frozenset({"echo_stream"}), classification=Classification.READ_ONLY,
    )
