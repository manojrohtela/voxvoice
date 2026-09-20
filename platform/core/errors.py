"""Typed platform error hierarchy (Constitution §A2.5 CODING_STANDARDS §3).

One root, `PlatformError`. Every layer raises typed errors; boundaries translate
them (provider errors -> capability errors; runtime errors -> typed events; API
errors -> HTTP codes). Raw third-party errors never cross a boundary.

Scaffolding established at M1; subclasses are added as the components that raise
them are built (runtime, providers). No behaviour here — definitions only.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Root of all VoxVoice-raised errors."""


class ConfigError(PlatformError):
    """Invalid or inconsistent configuration (fail fast, do not run degraded)."""


class ProviderError(PlatformError):
    """A capability provider failed. Wrapped by the fault-tolerance layer; a raw
    vendor error must never reach a consumer (Constitution §A10, §A6)."""


class RuntimeConflict(PlatformError):
    """An illegal state-machine transition or lifecycle violation
    (Constitution §A4.4). Raised by the runtime once it exists (M2+)."""


# ── the provider-neutral failure taxonomy (M11.4, ADR-0046 FT-D1) ────────────
#
# Small and composable on purpose. These are the *kinds* of failure the runtime can act on
# differently — a timeout is retryable-by-failover, a credential error is not — and nothing
# finer, because a taxonomy the runtime cannot branch on is decoration.
#
# **Vendor mapping happens inside the vendor's plugin, never here.** A plugin catches its SDK's
# exceptions and raises one of these; `runtime/` and `core/` therefore contain no `if vendor ==`
# branch (AR-21), and no raw third-party exception ever crosses a boundary (§A10, CODING_STANDARDS
# §3, SECURITY_POLICY §5 — vendor errors routinely embed endpoints and credential fragments).
#
# `asyncio.CancelledError` is deliberately **absent**. Cancellation is not a provider failure: it
# is the caller withdrawing, and converting it into one would make a cancelled turn look like a
# broken vendor and would corrupt the breaker's failure count.


class ProviderTimeout(ProviderError):
    """A provider call exceeded its deadline. Distinct from silence: a timeout is a *decision*
    the runtime made, and it must never be mistaken for a caller hangup."""


class ProviderUnavailable(ProviderError):
    """The provider could not be reached, or dropped the connection mid-operation."""


class ProviderAuthError(ProviderError):
    """Credentials were rejected. Never carries the credential — see ADR-0049 CR5/CR6."""


class ProviderRateLimited(ProviderError):
    """The provider refused the call for quota or rate reasons."""


class ProviderProtocolError(ProviderError):
    """The provider answered, but not in a shape the contract admits."""


class ProviderStreamTruncated(ProviderError):
    """A provider stream ended before its natural end.

    Its own type because the alternative is catastrophic: `RuntimePipeline.run()` treats
    exhaustion of the STT stream as **caller hangup**, so a vendor disconnect that surfaced as
    ordinary exhaustion would end the call and report the wrong real-world fact (AR-6,
    ADR-0045 SD4)."""


class CapabilityUnsupported(ProviderError):
    """The provider cannot satisfy what the configuration asked of it.

    Named `Capability*` rather than `Provider*` to stay disjoint from
    `runtime.errors.ProviderCapabilityMismatch`, which is the *registry's* registration-time
    error and a different concern (§A0 — one name per concept)."""


class ProvidersExhausted(ProviderError):
    """Every candidate in the ordered failover chain has been tried and none succeeded.

    A clean, typed terminus for the §A10 degradation ladder — never a hang, never a silent
    fallthrough."""
