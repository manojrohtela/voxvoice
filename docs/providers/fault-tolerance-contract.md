# Provider Fault-Tolerance Contract

**Status:** Active v1.0 (2026-09-07) · **Owner:** Technical Owner · **Milestone:** M11.4
**Authority:** [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [ADR-0045](../adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md), Constitution §A6.2 / §A10, AR-23, AR-24, AR-25, DL-0013

What a provider plugin owes the runtime, and what the runtime guarantees in return, before the
first real vendor lands at M11.5.

## The rule this implements

**AR-23:** *"Every provider call passes through the fault-tolerance layer — timeout, typed error,
circuit breaker, optional failover chain. **Direct raw calls are defects.**"*

Until M11.4 the pipeline called every capability directly. That was harmless against the
deterministic local profile and a standing defect the moment a vendor could time out, disconnect,
rate-limit or lie.

## What the runtime guarantees

Every capability call in `RuntimePipeline` passes through `runtime.faults.ProviderGuard`:

| Concern | Behaviour |
|---|---|
| **Timeout** | Per-call and per-stream-item deadlines. Exceeding one raises `ProviderTimeout`. |
| **Typed error** | Any escaping exception becomes a `core.errors.ProviderError` subclass. The `__cause__` is preserved. |
| **Circuit breaker** | `provider.CircuitBreaker`, **reused not reimplemented**, with an injected clock. |
| **Failover** | `provider.FailoverController` walks an ordered chain, each candidate at most once. |
| **Cancellation** | `asyncio.CancelledError` propagates untouched and never counts as failure. |
| **Closure** | A guarded stream always closes its source, including on early consumer exit. |

### Three rules that are easy to get subtly wrong

1. **A timeout is not a hangup.** `RuntimePipeline.run()` reads exhaustion of the inbound or STT
   stream as *the caller hung up*. A deadline that let a stream simply end would emit the wrong
   lifecycle fact (**AR-6**). Timeouts raise; they never end a stream quietly.
2. **Cancellation is not a failure.** The caller withdrawing is not the vendor misbehaving, so it
   is never translated and never counted against a breaker.
3. **Failover is selection, not retry.** ADR-0046 **FT-D5** authorizes walking forward to the next
   untried candidate. Nothing sleeps, backs off, or re-attempts a failed candidate. Exhaustion is
   `ProvidersExhausted` — a typed terminus, never a hang.

## What a provider plugin owes

### Map your own SDK

**AR-12** confines a vendor SDK to its plugin, and §A10 / SECURITY_POLICY §5 forbid a raw vendor
error from crossing a boundary — vendor exceptions routinely embed endpoints, account identifiers
and occasionally credential fragments. So the plugin catches its SDK's exceptions and raises one
of these:

| Raise | When |
|---|---|
| `ProviderTimeout` | the vendor exceeded its own deadline |
| `ProviderUnavailable` | unreachable, or the connection dropped |
| `ProviderAuthError` | credentials rejected — **never** carrying the credential |
| `ProviderRateLimited` | quota or rate refusal |
| `ProviderProtocolError` | answered, but not in a shape the contract admits |
| `ProviderStreamTruncated` | a stream ended before its natural end |
| `CapabilityUnsupported` | the configuration asked for something this provider cannot do |

The guard translates anything that escapes, but that is a **backstop, not the plan**: a plugin
relying on it produces `ProviderUnavailable` for everything and throws away the distinction the
runtime needs to behave differently.

### Declare capabilities truthfully

**ADR-0045 SD9** makes an untruthful flag merge-blocking, and the M11.3 conformance suite asserts
it. A provider that cannot stream declares `streaming=False` and runs in **declared degraded
mode** (§A6.4) — the runtime adapts and records it. Under-delivering while declaring otherwise is
the failure mode; declaring a limit honestly is not.

## Degradation (AR-24)

*"Degradations are declared, evented, and recorded in session metadata — never silent."*

`DegradationRecorder` gives every session a bounded, inspectable record. Kinds: `not_streaming`,
`unavailable`, `timeout`, `disconnected`, `capability_mismatch`, `auxiliary_disabled`,
`failed_over`, `exhausted`.

The history is bounded (a degrading session is the one most likely to degrade repeatedly) while
the **counts are not**, so a long outage does not look smaller than a brief one after truncation.

**Auxiliary capabilities degrade rather than break** (DL-0013). Voice activity is the standing
example: a VAD failure must never end a call — and, since M11.4, must never be invisible either.
Before this the failure was a log line, which is exactly the silent degradation AR-24 forbids.

Degradations publish onto the **existing** in-process event seam. The Epic 10 durable spine stays
unwired until M11.12 (ADR-0050 SW1); there is no second telemetry path.

## Configuration (AR-25)

*"Accepted config must act — every accepted field is honoured or the write is rejected."*

`runtime.faults.validation` refuses impossible configuration at session start rather than
accepting and ignoring it: unregistered provider selections, out-of-range sample rates, a model
the registered provider does not declare, interruptions with no voice-activity provider to detect
them, and invalid timeout/breaker policy. Every issue is reported at once — a validator that stops
at the first makes fixing a config a guessing game.

**Capability truth belongs to the provider** (AR-22). The validator asks the registry what the
provider *declared*; it contains no vendor name and must never gain one (AR-21). A provider that
enumerates no models is declaring *"I do not enumerate"*, and any selection is then accepted.

### `first_message` is declared pending, not played

M11.4 does not play the greeting — **D-11** assigns it to M11.10's Playback Scheduler and its §A8
greeting pre-cache. Leaving the field unread was not an option either (AR-25), so it carries an
explicit `GreetingState`: `ABSENT` or `PENDING_PLAYBACK`. There is deliberately **no state
claiming audio was produced**.

## What M11.4 did not do

No WebRTC, no real STT/TTS/LLM/VAD, no Playback Engine or Scheduler, no Epic 10 spine wiring, no
latency baseline, no Stage-2 deployment. **No vendor capability was measured** — the
target-language streaming question remains deferred (DL-0154), and the degradation machinery above
is the mechanism that will record the answer truthfully once it is.
