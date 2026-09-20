# Runtime Hardening (M11.15)

**Status:** Active v1.0 (2026-09-19) · **Owner:** Technical Owner · **Milestone:** M11.15
**Authority:** Constitution v2 **§A10**, **§A8**, **§A11.1.7**; [ADR-0035](../adr/ADR-0035-durable-write-interposition-on-the-event-bus.md) **DW2/DW4**, [ADR-0036](../adr/ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md), [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md) **FT-D5/FT-D6**, [ADR-0047](../adr/ADR-0047-audio-driven-endpointing-and-metric-turn.md) **ME8**, [ADR-0049](../adr/ADR-0049-provider-credential-injection.md); **AR-23, AR-24, AR-31, AR-41**; DL-0013
**Companion:** [Runtime Resilience and the §A10 Ladder](resilience.md) — M11.14's behaviour; this is what was hardened afterwards.
**Decisions:** DL-0211 (R-48), DL-0212 (R-69), DL-0213 (R-70)

> **This does not make VoxVoice production-ready.** It closes three of the four risks M11.14 left
> open, under hermetic conditions on one machine. No live vendor, no real network peer and no load
> were involved.

## Scope

M11.14 asked *"does the system behave correctly when components fail?"* M11.15 asks whether that
behaviour survives edge cases, concurrency, repeated degradation and lifecycle races. It changes
no architecture, adds no framework, and introduces no dependency.

## R-48 — sustained degradation is an episode, not an event · **CLOSED**

### The problem, as measured

A VAD that cannot answer degraded **once per inbound frame** — at the 20 ms media cadence, ~50
publications per second per session for the length of the outage. The retained history was
bounded, so memory was safe, but a 64-entry window rolled over in about 1.3 seconds, destroying
the record of everything else that happened during the incident. Bounded recording, unbounded
telling.

### The semantic, taken from the repository rather than invented

`InterruptPolicy.duplicate_suppression` already says *"collapse repeat confirms within one
assistant utterance"*. A degradation is now the same shape: an **episode**, keyed by
`(capability, kind, provider)`.

There is **no rate limit, no time window and no sampling ratio** — no number to tune, which §5
required.

| Situation | Behaviour |
|---|---|
| first occurrence | published, retained, counted |
| repeat while the episode is open | **counted only** — no publication, no history entry |
| the capability succeeds again | the episode ends; **nothing is published** |
| a new failure after recovery | published again, as a new episode |
| a different `kind` or `provider` | a different episode — published |
| a different `detail` | the *same* episode — vendor text varies per frame |
| another session | fully independent; the recorder is per session |

`detail` is deliberately excluded from the key: it carries the vendor's message, which can differ
on every frame, and keying on it would defeat coalescing for exactly the noisy failures that
motivated it. The **first** occurrence's detail is retained — it is the one nobody has seen.

### Recovery signal

Reused, not invented. `ProviderGuard` already treats a successful call as *"this provider is
usable again"* for the circuit breaker; `_on_success` now also ends the episode. Auxiliary
capabilities never reach `call()`/`stream()` — the voice-activity tap invokes the provider inline
and swallows failures by design (DL-0013) — so `auxiliary_recovered()` supplies the missing
signal from the tap after a frame is analysed without raising.

**No recovery is ever announced.** `resolved()` publishes nothing and claims nothing: one
successful call is not health, and a `recovered` event would be the false recovery §5 forbids.
Whether a degradation is *still happening* is answered by `is_active()`, so suppression can never
be mistaken for recovery.

### What is preserved

Counts are incremented **before** any suppression decision, so coalescing can never make an
outage look smaller than it was. `suppressed` exposes how many repeats were collapsed.

## R-69 — durable-write failure · **STILL OPEN, narrowed and now deterministic**

### What the contracts already say

ADR-0035 **DW4** is explicit: *"Do not dispatch the failed event. No subscriber may observe an
event that is not durable."* Nothing was invented here; the behaviour was **discovered** by
injecting the failure and reading the contract that explains it.

| § | Question | Answer |
|---|---|---|
| A | publish succeeds, durable write fails | event is **not dispatched**; failure hook fires once; `seq` is not rewound; the producer sees no exception (AR-31) |
| B | repeated failures | each accounted, each notified; **no retry in-session** |
| C | sink recovers | new events dispatch again; events lost during the outage are **not** replayed into the session |
| D | session continues while degraded | **yes** — availability is not conditional on the event log |
| E | session terminates while degraded | terminates cleanly; its terminal events are also undispatched |
| F | shutdown while writes fail | completes; a session cannot be stranded by a storage incident |
| G | duplicate/retry | none in-session |
| H | ordering | `seq` is not rewound, so a gap is visible to a reader; read-back stays `seq`-ordered |

### The consequence worth stating plainly

While the durable sink is failing, the in-process bus goes **entirely silent**. Every consumer —
the metric buffer, the gateway, operator tooling — sees nothing at all. **The runtime stays
available; its observability does not.** That is DW4 working as written, and it is the most
operationally significant thing in this document.

It also has a testing consequence, recorded because it will surprise the next person: any harness
that waits on a bus event cannot be used while a durable sink is failing, because the event it
waits for is contractually forbidden from arriving.

### Why the risk stays open

In-session **recovery** remains undefined, and deliberately so. ADR-0035 declares M10.6
implements no recovery; the outbox retry ladder (ADR-0036) and recovery driver (ADR-0037) exist
but are **not driven from a live conversation**. Defining one here would have been the second
durable queue §6 forbids. **R-69 is narrowed from *"undefined"* to *"defined everywhere except
in-session replay, which is Epic 10's to define."***

## R-70 — harness synchronization · **CLOSED**

### The problem

M11.11's harness waited a fixed number of event-loop spins. That is a bet on scheduling, and an
unwinnable one: `SileroVoiceActivity` runs inference on a **worker thread**, so the iterations
between pushing audio and a turn finishing depend on the OS, not the runtime.

### The fix: wait on runtime facts

Two facts replaced the budget, and both already existed:

1. **`turn.completed` / `turn.cancelled`** — published by the Turn Engine on the session's own
   bus. `turn.completed` is stamped after the clause router finishes and the Playback Engine
   drains, so every frame of the answer has reached the transport by then. Both terminal phases
   are awaited, because a resilience test often expects the turn to end by *failing*.
2. **`SileroVoiceActivity.buffered_ms()`** — the provider's existing observability. Frames are
   paced against it so the analysis lag is exactly one frame, regardless of how many sessions
   interleave or how loaded the machine is.

Timeouts remain, but only as **deadlines**: correctness comes from the fact being waited on, and
the deadline exists so a stuck runtime fails instead of hanging.

### One additive observability method

`SileroVoiceActivity.inference_failed(session_id)` reports a fact the session already holds. It is
what makes the other two counters interpretable: once inference stops, `buffered_ms` never falls
again, so *"audio is still buffered"* means *"the worker is behind"* only while this is `False`.
Without it, a dead worker and a slow one can be told apart only by waiting — a timeout pretending
to be a diagnosis, which cost seven tests ten seconds each.

### Evidence

`test_resilience.py` + `test_e2e_conversation.py` + `test_epic10_runtime_wiring.py`, **ten
consecutive runs under 8-way CPU contention on a 10-core machine: 152 passed every time.** Before
the fix the same command failed 1–2 tests in most runs. An AST guard asserts no `settle(spins=…)`
remains in either conversation harness.

## R-68 — failover coverage · **STILL OPEN (deployment, not mechanism)**

The chain is now exercised hermetically against the real `FailoverController`: first candidate
fails → second selected and succeeds; all fail → `ProvidersExhausted`; auth failure on the first
still reaches the second; unsupported capability falls over without retrying the same candidate;
cancellation does **not** walk the chain; a succeeding first candidate never reaches the second.

**The mechanism is correct. The risk is not about the mechanism.** No deployed composition
configures more than one provider per capability, so rung 1 of the §A10 ladder has still never run
outside a test. A hermetic fake-provider test is not deployed failover, and §29 is explicit that
it must not be classified as one.

## Lifecycle and ownership

Asserted rather than assumed:

* the VAD **worker thread** is released with its session — 20 sessions, no thread accumulation;
* a stopped session holds no buffered audio;
* a session that fails and is then stopped closes its transport **exactly once**;
* `PlaybackEngine` terminal states are idempotent under repeated `stop()`, and `cancel()` after
  `FAILED` is clean;
* a failed consumer never leaves a producer blocked;
* turn retirement survives five failure/recovery alternations in one session.

## Security and the failure-event boundary

The degradation payload remains a closed set of **five string fields**. M11.14 proved the *shape*;
M11.15 proves the *values*: a vendor exception carrying a credential in its message, its `repr`
and an attribute is raised through the guard, and neither the secret nor the vendor endpoint
reaches the payload. An AST guard asserts no provider reads `os.environ` — credentials arrive
through `CredentialProvider` (ADR-0049) or not at all.

## Configuration and input validation

Impossible values are **refused, not defaulted**: zero/negative guard timeouts, zero/negative
playback capacity, a zero frame ceiling, and out-of-range session sample rates (rejected before
the session exists). Malformed audio — odd byte length, zero length, zero channels — passes
through the single ingress conversion unchanged rather than raising into the conversation, and is
rejected with a **typed** capability error at the provider boundary, which is where format
validation belongs. **The `AudioFrame` contract was not changed.**

## Test determinism

No test added or modified here sleeps for a duration, waits a fixed number of loop iterations, or
retries until it passes. Waits are on: a runtime event, a provider's own counter, or an explicit
failure deadline.

## Remaining risks

| Risk | Disposition |
|---|---|
| **R-48** sustained VAD degradation noise | **CLOSED** — episode coalescing, evidence in `test_hardening.py` |
| **R-70** harness synchronization | **CLOSED** — runtime-fact waits, 10 clean runs under contention |
| **R-69** durable-write recovery | **STILL OPEN**, narrowed to in-session replay only |
| **R-68** failover coverage | **STILL OPEN** — mechanism proven, deployment composition unchanged |
| **R-56 / R-61** no mid-stream LLM / mid-utterance TTS failover | **CONFIRMED AS DESIGNED** |
| **R-66** `stt` unmeasurable, `llm` absorbs the endpoint hold | **STILL OPEN** — instrumentation limitation, untouched |
| **R-47, R-49, R-51–R-55, R-57–R-60, R-62–R-65, R-67** | **STILL OPEN** — unchanged by this milestone |

Live-provider behaviour is **NOT EXECUTED** throughout: no credential was available for Deepgram,
Groq or Cartesia, so R-54, R-58 and R-63 stand untouched.
