"""`ProviderGuard` — the media path's fault-tolerance seam (M11.4, ADR-0046 **FT-D1/FT-D4**).

**AR-23:** *"Every provider call passes through the fault-tolerance layer — timeout, typed error,
circuit breaker, optional failover chain. **Direct raw calls are defects.**"*

Until M11.4 the pipeline called `stt.transcribe(...)`, `llm.stream(...)`, `tts.synthesize(...)`,
`transport.send(...)` and `transport.inbound(...)` directly: no deadline, no typed translation, no
breaker, no failover. Harmless against the deterministic local profile; a standing defect the
moment a real vendor lands at M11.5.

## What this is, and what it deliberately is not

It is a **thin decorator around the existing capability contracts**. It constructs no provider,
holds no registry, resolves nothing by vendor name and contains no `if vendor ==` branch (AR-21).

It is **not** a second Provider Runtime. ADR-0046 **FT-D2** rules out routing the media path
through `platform/provider/`'s execution model — that seam is synchronous
(`AdapterPort.stream() -> Iterator`), request/response-shaped, and declared architecturally
closed, while a media session is duplex and lives for minutes. What *is* reused, per **FT-D3**,
is the mechanism: `provider.CircuitBreaker` (stdlib-only, injected clock, no timers) and
`provider.FailoverController` (deterministic chain walk, no retry loops), both imported rather
than reimplemented.

## The three rules that shape every method below

1. **A timeout is not a hangup.** `RuntimePipeline.run()` reads exhaustion of the inbound or STT
   stream as *caller hung up*. A deadline that let a stream simply end would report the wrong
   real-world fact (AR-6). Timeouts raise `ProviderTimeout`; truncated streams raise
   `ProviderStreamTruncated`.
2. **Cancellation is not a failure.** `asyncio.CancelledError` propagates untouched, is never
   translated, and never counts against a breaker — the caller withdrawing is not the vendor
   misbehaving.
3. **No retry loops.** ADR-0046 **FT-D5** authorizes *selection*, not repetition: failover walks
   forward to the next untried candidate and stops. Nothing here sleeps, backs off, or re-attempts
   a candidate that already failed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from core.errors import (
    ProviderError,
    ProvidersExhausted,
    ProviderStreamTruncated,
    ProviderTimeout,
    ProviderUnavailable,
)
from provider.circuit_breaker import BreakerState, CircuitBreaker, CircuitBreakerPolicy
from provider.failover import FailoverController, FailoverReason

from .degradation import Degradation, DegradationKind, DegradationRecorder

log = logging.getLogger("voxvoice.runtime.faults")

T = TypeVar("T")

#: Default deadline for a single non-streaming provider call, in seconds. A *bound*, not a tuning
#: knob: §A10 requires "timeouts on every external call" and PERFORMANCE_POLICY §3 repeats it.
DEFAULT_CALL_TIMEOUT_S = 10.0

#: Default deadline for the **next item** of a provider stream. Separate from the call timeout
#: because a stream is legitimately long-lived while any single silence between items is not.
DEFAULT_STREAM_IDLE_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class GuardPolicy:
    """The fault-tolerance settings for one capability. Validated, never silently coerced."""

    call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S
    stream_idle_timeout_s: float = DEFAULT_STREAM_IDLE_TIMEOUT_S
    breaker: CircuitBreakerPolicy | None = None

    def __post_init__(self) -> None:
        # AR-25 in miniature: a configuration field that is accepted and then ignored is the
        # defect this whole milestone exists to remove, so an impossible value is refused here
        # rather than quietly replaced with a default.
        if self.call_timeout_s <= 0:
            raise ValueError(f"call_timeout_s must be positive, got {self.call_timeout_s}")
        if self.stream_idle_timeout_s <= 0:
            raise ValueError(
                f"stream_idle_timeout_s must be positive, got {self.stream_idle_timeout_s}"
            )


class CandidateList:
    """An ordered chain satisfying `core.failover.CandidateChain`.

    A four-line value type rather than a reuse of `provider.RoutingDecision`, because importing
    that would reintroduce exactly the transitive coupling ADR-0046 FT-V2 measured and FT-D3
    removed."""

    __slots__ = ("_candidates",)

    def __init__(self, candidates: Sequence[str]) -> None:
        self._candidates = tuple(candidates)

    @property
    def candidates(self) -> tuple[str, ...]:
        return self._candidates


class ProviderGuard:
    """Applies timeout + typed error + breaker + ordered failover to capability calls."""

    def __init__(
        self,
        *,
        capability: str,
        policy: GuardPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        failover: FailoverController | None = None,
        degradations: DegradationRecorder | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._capability = capability
        self._policy = policy or GuardPolicy()
        # The breaker is *reused*, never reimplemented (FT-D3). Its clock is injected all the way
        # through, which is what makes breaker behaviour testable without a single sleep.
        self._breaker = breaker or CircuitBreaker(self._policy.breaker, clock=clock)
        self._failover = failover or FailoverController()
        self._degradations = degradations

    @property
    def capability(self) -> str:
        return self._capability

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def breaker_state(self, provider: str) -> BreakerState:
        return self._breaker.state_for(provider)

    # ── single calls ─────────────────────────────────────────────────────────

    async def call(
        self, provider: str, operation: Callable[[], Awaitable[T]], *, what: str = "call"
    ) -> T:
        """Run one provider call under a deadline, a breaker and typed translation."""
        self._require_closed(provider, what)
        try:
            # Bound to a name and returned *after* recording success. An earlier draft returned
            # directly from the `try` and recorded success in an `else:` clause — which never
            # ran, so a healthy provider never cleared its breaker and a HALF_OPEN breaker could
            # not close. The conformance test caught it; the shape below is why it cannot recur.
            result = await asyncio.wait_for(operation(), timeout=self._policy.call_timeout_s)
        except TimeoutError as exc:
            self._on_failure(provider)
            self._degrade(DegradationKind.TIMEOUT, provider, f"{what} exceeded its deadline")
            raise ProviderTimeout(
                f"{self._capability}/{provider}: {what} exceeded "
                f"{self._policy.call_timeout_s}s"
            ) from exc
        except asyncio.CancelledError:
            # Rule 2. Not a failure, not translated, not counted — re-raised exactly as received.
            raise
        except ProviderError as exc:
            self._on_failure(provider)
            # Recorded, not merely re-raised (M11.5). A plugin that maps its own SDK properly
            # (AR-12) produces a *typed* error, and leaving that unrecorded meant a well-behaved
            # plugin degraded **invisibly** while a leaky one did not — the inconsistency AR-24
            # exists to forbid. The error keeps its own type; only the record is added.
            self._degrade(DegradationKind.UNAVAILABLE, provider, f"{what}: {type(exc).__name__}")
            raise
        except Exception as exc:  # noqa: BLE001 — a plugin that leaked a raw error still must not
            # reach the conversation untyped (§A10, SECURITY_POLICY §5). Translating here is the
            # backstop; the plugin remains responsible for mapping its own SDK (AR-12).
            self._on_failure(provider)
            self._degrade(DegradationKind.UNAVAILABLE, provider, f"{what} failed")
            raise ProviderUnavailable(f"{self._capability}/{provider}: {what} failed") from exc
        self._on_success(provider)
        return result

    async def call_with_failover(
        self,
        chain: Sequence[str],
        operation: Callable[[str], Awaitable[T]],
        *,
        what: str = "call",
    ) -> T:
        """Walk an ordered chain, best first, until one candidate succeeds.

        **Selection, not retry** (FT-D5): each candidate is tried at most once, in order, with no
        backoff and no sleeping. Exhaustion is a typed terminus, never a hang."""
        attempted: tuple[str, ...] = ()
        last: ProviderError | None = None
        candidates = CandidateList(chain)
        while True:
            outcome = self._failover.next(
                candidates, attempted=attempted, reason=FailoverReason.UNAVAILABLE
            )
            if outcome.exhausted or outcome.candidate is None:
                self._degrade(
                    DegradationKind.EXHAUSTED,
                    ",".join(chain),
                    f"every candidate failed for {what}",
                )
                raise ProvidersExhausted(
                    f"{self._capability}: no candidate succeeded for {what} "
                    f"(tried {list(attempted)})"
                ) from last
            attempted = outcome.attempted
            candidate = outcome.candidate
            try:
                return await self.call(candidate, lambda c=candidate: operation(c), what=what)
            except asyncio.CancelledError:
                raise  # Rule 2 — never failed over on
            except ProviderError as exc:
                last = exc
                if len(attempted) < len(candidates.candidates):
                    self._degrade(
                        DegradationKind.FAILED_OVER, candidate, f"{what}: {type(exc).__name__}"
                    )

    # ── streams ──────────────────────────────────────────────────────────────

    async def stream(
        self, provider: str, source: AsyncIterator[T], *, what: str = "stream"
    ) -> AsyncIterator[T]:
        """Guard a provider stream: per-item deadline, typed translation, deterministic close.

        The item deadline is what turns vendor *silence* into a decision. Without it a stalled
        socket is indistinguishable from a caller who simply stopped talking.

        `finally: aclose()` is not decoration — it is what makes generator closure deterministic
        and stops a guarded stream from leaving the provider's own task running when the consumer
        breaks out early (§A11.1.7)."""
        self._require_closed(provider, what)
        iterator = source.__aiter__()
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        iterator.__anext__(), timeout=self._policy.stream_idle_timeout_s
                    )
                except StopAsyncIteration:
                    self._on_success(provider)
                    return  # the stream's natural end — the only clean exhaustion
                except TimeoutError as exc:
                    self._on_failure(provider)
                    self._degrade(
                        DegradationKind.TIMEOUT, provider, f"{what} stalled between items"
                    )
                    raise ProviderTimeout(
                        f"{self._capability}/{provider}: {what} produced no item within "
                        f"{self._policy.stream_idle_timeout_s}s"
                    ) from exc
                except asyncio.CancelledError:
                    raise  # Rule 2
                except ProviderError as exc:
                    self._on_failure(provider)
                    self._degrade(
                        DegradationKind.UNAVAILABLE, provider, f"{what}: {type(exc).__name__}"
                    )
                    raise
                except Exception as exc:  # noqa: BLE001 — rule 1: an abrupt end is *not*
                    # exhaustion. Surfacing it as `ProviderStreamTruncated` is what keeps a vendor
                    # disconnect distinguishable from a caller hangup (ADR-0045 SD4, AR-6).
                    self._on_failure(provider)
                    self._degrade(DegradationKind.DISCONNECTED, provider, f"{what} ended abruptly")
                    raise ProviderStreamTruncated(
                        f"{self._capability}/{provider}: {what} ended unexpectedly"
                    ) from exc
                yield item
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()

    # ── auxiliary capabilities (DL-0013) ─────────────────────────────────────

    def auxiliary_failed(self, provider: str, detail: str, *, fallback: str) -> Degradation:
        """An auxiliary capability failed and the conversation continues without it.

        DL-0013's rule, made explicit and *recorded*: degrading is permitted, **pretending is
        not**. Voice activity is the standing example — a VAD failure must never end a call, and
        must never look like a VAD that worked."""
        return self._degrade(
            DegradationKind.AUXILIARY_DISABLED, provider, detail, fallback=fallback
        )

    def auxiliary_recovered(self, provider: str) -> None:
        """An auxiliary capability produced a usable result again (M11.15, R-48).

        Auxiliary capabilities never reach `call()` or `stream()` — the voice-activity tap invokes
        the provider inline and swallows its failures by design (DL-0013) — so they have no
        success path to piggyback on and would otherwise stay suppressed for the rest of the
        session after one bad frame. This is that missing signal, and nothing more: it publishes
        no event and claims no health."""
        if self._degradations is not None:
            self._degradations.resolved(self._capability, provider)

    def declared_limit(self, provider: str, kind: DegradationKind, detail: str) -> Degradation:
        """A provider truthfully declared it cannot do something (§A6.4, ADR-0045 SD9).

        Not a failure — the honest half of the capability contract. Recorded so *"we ran without
        streaming"* is answerable after the call rather than inferred from a vendor's docs."""
        return self._degrade(kind, provider, detail)

    # ── internals ────────────────────────────────────────────────────────────

    def _require_closed(self, provider: str, what: str) -> None:
        if not self._breaker.allow(provider):
            self._degrade(
                DegradationKind.UNAVAILABLE, provider, f"{what} refused: circuit breaker open"
            )
            raise ProviderUnavailable(
                f"{self._capability}/{provider}: circuit breaker is "
                f"{self._breaker.state_for(provider).value}"
            )

    def _on_failure(self, provider: str) -> None:
        self._breaker.record_failure(provider)

    def _on_success(self, provider: str) -> None:
        """The provider worked: clear the breaker and end any open degradation episode (M11.15).

        The two belong together. The breaker already treats success as the signal that a provider
        is usable again, and R-48's episode suppression needs exactly the same signal to re-arm
        reporting — so this reuses it rather than introducing a second notion of recovery.
        Nothing is published here: one successful call is not a claim of health (§5)."""
        self._breaker.record_success(provider)
        if self._degradations is not None:
            self._degradations.resolved(self._capability, provider)

    def _degrade(
        self, kind: DegradationKind, provider: str, detail: str, *, fallback: str = ""
    ) -> Degradation:
        degradation = Degradation(
            capability=self._capability,
            kind=kind,
            detail=detail,
            provider=provider,
            fallback=fallback,
        )
        if self._degradations is not None:
            self._degradations.record(degradation)
        return degradation


__all__ = [
    "DEFAULT_CALL_TIMEOUT_S",
    "DEFAULT_STREAM_IDLE_TIMEOUT_S",
    "CandidateList",
    "GuardPolicy",
    "ProviderGuard",
]
