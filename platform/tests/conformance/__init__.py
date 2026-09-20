"""The Epic 11 capability conformance suite (M11.3).

Constitution **§A6.3** makes this a constitutional requirement, not a convenience:

> Adding a provider = one module + one registry entry + **passing the shared per-capability
> contract test suite**. Nothing else changes.

TESTING_POLICY's *contract (capability)* tier says what it must verify — *"streaming semantics,
partials, error typing, timeout behaviour, capability flags truthfulness"* — and ratified
[ADR-0045](../../../docs/adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md) **SD9** makes
an untruthful capability flag a **merge-blocking defect** rather than a documentation nit.

## What this package is

One suite per capability, **parameterized over every registered provider**. The local profile is
the only provider set today; a real vendor added at M11.5+ is registered here and judged by the
same assertions, on the same day it lands. That ordering is deliberate — a conformance suite
written *after* the first vendor tends to describe that vendor.

## What it is not

It is not a second testing architecture. It reuses the repository's existing shapes: plain pytest
modules under `tests/`, AST/structural inspection where a property is structural (the
`test_events_boundary.py` and `tests/freeze/**` precedent), and hermetic in-process providers with
no vendor, no network and no credentials (TESTING_POLICY rule 1).

## On measuring "O(1)" without a flaky clock

ADR-0045 **SD7** specifies `detect()` as O(1) and non-blocking. Asserting that with a wall-clock
threshold would be exactly the kind of timing-dependent test TESTING_POLICY rule 2 rejects. This
suite therefore measures it **structurally and by instrumentation** — counting the work a call
actually performs against an injected, deterministic accountant — and uses a clock only for a
coarse, generously-bounded sanity check that cannot realistically flake.
"""

from .capabilities import (
    CONFORMANCE_PROVIDERS,
    ProviderUnderTest,
    llm_providers,
    stt_providers,
    transport_providers,
    tts_providers,
    voice_activity_providers,
)

__all__ = [
    "CONFORMANCE_PROVIDERS",
    "ProviderUnderTest",
    "llm_providers",
    "stt_providers",
    "transport_providers",
    "tts_providers",
    "voice_activity_providers",
]
