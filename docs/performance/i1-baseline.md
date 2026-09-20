# I1 Latency Baseline — Methodology and Results

**Status:** Active v1.0 (2026-09-19) · **Owner:** Technical Owner · **Milestone:** M11.13
**Authority:** Constitution §A8 (per-stage budgets, I1), AR-42; [ADR-0038](../adr/ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md), [ADR-0047](../adr/ADR-0047-audio-driven-endpointing-and-metric-turn.md) ME6
**Decisions:** DL-0205 (methodology), DL-0206 (findings), DL-0207 (limitations and risks)

> **This is a baseline, not a certification.** Nothing here establishes that VoxVoice is
> production-ready, low-latency, scalable or reliable. Every number below was produced on one
> developer laptop with **no live vendor and no network**, and the milestone that would change
> that has not run.

## What was measured, and by what

The harness drives the **production runtime** — `RuntimeSessionService` → `SessionWorker` →
`ConversationOrchestrator` → `RuntimePipeline` — and reads the runtime's own `metric.turn` through
the M11.12 observability plane. It adds **no second timing chain, no second clock and no metric of
its own**; `observability/turn_latency.py` remains the sole producer.

That is deliberate: a harness that timed turns with its own stopwatch would describe the harness.
Reading `metric.turn` means these are the numbers production will report, and a broken tap shows
up as a *missing boundary* rather than as a plausible number.

## Profiles

| Profile | Network | Providers | What it is evidence of |
|---|---|---|---|
| **A — hermetic** | none | return immediately | the runtime's own cost on this machine |
| **B — simulated** | none | wait a **configured** time | that each delay lands in the stage the runtime attributes it to |
| **C — live** | — | — | **NOT EXECUTED** |

Profile B's delays (`stt_final 40 ms`, `llm_first_token 120 ms`, `tts_first_audio 80 ms`,
`transport_send 5 ms`) are the *harness's own numbers*, chosen to be mutually distinguishable so a
misattributed delay is obvious. They are not any vendor's measured latency and must never be
quoted as one.

## Sampling

- **55 measured sessions × 2 turns = 110 turns** per profile, plus **3 warmup sessions**.
- 110 was chosen as the smallest sample that makes **p99 a real rank** rather than a restatement
  of the maximum (the tooling withholds p99 below 100 samples and p95 below 20, printing `—`).
- The **first** session is reported separately as *cold*: model load, imports and caches unfilled.
  Mixing one cold session into a warm distribution moves the tail and explains nothing.
- Frames are delivered at **real audio cadence (20 ms/frame)**, because a caller produces audio in
  real time — see the findings below for why this is load-bearing rather than cosmetic.

## Results

The current machine-readable artifact is [`i1-baseline.json`](i1-baseline.json); it carries the
environment, git revision, configuration, every measurement and the live/Stage-2 status. The
summary table is reproduced by the command in *Reproduction* below.

### Latency decomposition (warm, per `metric.turn` stage names)

Warm, **n = 110 turns per profile**, 0 failed sessions, no missing boundaries. Milliseconds.

| Stage | Meaning | A p50 | A p95 | A p99 | B p50 | B p95 | B p99 |
|---|---|---:|---:|---:|---:|---:|---:|
| `endpoint` | `speech_end → endpoint_decided` | 240 | 240 | 240 | 240 | 240 | 240 |
| `stt` | `endpoint_decided → stt_final` | **NOT MEASURED** | — | — | **NOT MEASURED** | — | — |
| `llm` | `stt_final → llm_first_token` | 287 | 300 | 302 | 374 | 383 | 384 |
| `tts` | `llm_first_token → tts_first_audio` | 0 | 0 | 2 | 80 | 81 | 83 |
| `egress` | `tts_first_audio → first_audio_out` | 0 | 1 | 1 | 5 | 8 | 12 |
| `voice_to_voice_ms` | `speech_end → first_audio_out` | **241** | 242 | 243 | **447** | 451 | 453 |

Additive, outside the chain: `user_speech` (`speech_start → speech_end`) p50 276 ms (A) / 279 ms
(B) — the harness's own scripted utterance length, not a system property.

Cold (first session, n = 2 turns) is in the artifact; it is within a few milliseconds of warm on
this machine because the deterministic providers do no I/O and the VAD model is a pure-Python
stand-in. **On a real deployment cold start would be dominated by the native model load and vendor
connection setup, neither of which this profile exercises.**

Environment: macOS 26.5.2, arm64, 10 CPU, Python 3.13.3 — recorded in the artifact with the git
revision, so a later run can be set beside it.

## Findings

### 1. `endpoint` ≈ 240 ms is the configured hold, not a cost

`speech_end` is **backdated** to true end-of-speech (M11.8) while `endpoint_decided` is stamped
when the decision is made — 240 ms of trailing silence later. So this stage measures
`trailing_silence_ms` almost exactly, and reducing it would be a policy change, not an
optimization. It sits inside §A8's *"endpoint decision ≤ 250 ms"* budget **by construction**,
which is not the same as having been validated against real speech (R-49 stands).

### 2. `stt` is NOT MEASURED, and `llm` is not LLM cost

Every turn in both profiles produced a **negative** `stt` interval, which the producer drops
(ADR-0047 ME6). The cause is structural: the recognizer finalizes when the caller stops speaking,
which is *before* the 240 ms hold elapses, so `stt_final` precedes `endpoint_decided`.

The consequence matters more than the gap itself: **`llm` absorbs the remainder of the endpoint
hold**. Profile A shows `llm` p50 = **287 ms** with a provider that returns *instantly*. Anyone reading
that as LLM latency would be wrong by the whole margin. This is recorded as **R-66** and is the
single most important caveat on this baseline.

### 3. The decomposition is otherwise sound

Injected delays land where they should, and the arithmetic closes:

- `tts_first_audio 80 ms` → `tts` p50 moves 0 → **80**;
- `transport_send 5 ms` → `egress` p50 moves 0 → **5**;
- `endpoint` is **unmoved** at 240 in both, as it must be — no provider is on that path;
- `voice_to_voice` moves 241 → **447**, i.e. +206 against 245 ms injected; the 39 ms difference is
  the STT delay shifting `stt_final` later *inside* an interval the hold already dominates, which
  is the same effect as finding 2.

A test asserts this attribution structurally with a wide margin, so it guards the instrument
without pinning a speed to this machine.

### 4. No Epic 10 telemetry overhead was detectable

Comparing session wall-clock with the observability plane attached and detached gives a **p50
difference of +0.3 ms per session** (n = 27 per arm; 1450.5 ms wired vs 1450.2 ms detached).

Wall-clock is unavoidable here — with telemetry off there is no `metric.turn` to compare on — so
both arms are dominated by the harness's paced frame delivery and only the *difference* means
anything. At +0.3 ms against a ~1450 ms session the difference is **within this harness's noise**,
so the honest reading is *no overhead was detected at this resolution*, not *the overhead is
0.3 ms*. An earlier 6-session run of the same comparison read +8 ms, which is the same conclusion
stated less precisely.

## Not measured

| Question | Status |
|---|---|
| Deepgram / Groq / Cartesia latency | **NOT EXECUTED** — no credential in this environment |
| Real WebRTC network path | **NOT EXECUTED** — no peer |
| Stage-2 host figures | **NOT EXECUTED** — no Oracle A1 class host provisioned |
| VAD accuracy | **not attempted** — R-47 stands; only processing cost is exercised |
| Endpoint threshold quality | **not attempted** — R-49 stands; thresholds were not retuned |
| Clause policy quality | **not attempted** — R-52/R-53 stand |
| Playback capacity tuning | **not attempted** — R-60 stands |
| Concurrency / throughput | out of scope |

The **1 GB E2 micro is reachable but was deliberately not used**: §19 of this milestone forbids
reusing it, and it is not the authorized Stage-2 class.

## Error statistics

Failed sessions are **counted, never silently dropped** — a failed run contributes to
`failed_sessions` and not to any percentile. Turns whose payload lacks a boundary are recorded in
`missing_boundaries`, so a missing tap can never masquerade as a fast sample.

## Security and privacy

The artifact contains identifiers, timings, counts and environment facts. It contains **no**
credential, audio, transcript or model output — asserted by test, including that the harness's own
synthetic utterance does not appear. Credential *presence* is reported by environment-variable
name only; no value is read.

## Reproduction

```bash
cd platform
uv sync                       # locked dependencies
.venv/bin/python -m benchmarks.baseline \
    --sessions 55 --turns 2 --warmup 3 \
    --json ../docs/performance/i1-baseline.json
```

Smaller runs are fine for a smoke check; the tooling will withhold percentiles the sample cannot
support rather than print a misleading number.

## What this baseline is for

To be re-run and compared. The artifact records the git revision, Python version, platform, CPU
count and configuration precisely so a later run — especially the first one with live providers or
on a Stage-2 host — can be set beside it. Until that run happens, §A8's budgets and the I1 target
remain **unvalidated**, and this document says so rather than implying otherwise.
