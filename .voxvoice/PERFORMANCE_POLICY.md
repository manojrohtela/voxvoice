# PERFORMANCE_POLICY.md — Performance Expectations & Gates

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · **Numbers live in the Constitution** (I1, A8) — this document defines how they are enforced. **Performance regressions block releases** (GOVERNANCE §10).

## 1. Latency budgets (authoritative source: Constitution A8)

- Voice-to-voice: **p50 ≤ 1.0 s, p95 ≤ 1.8 s**. Per-stage p50: endpoint decision ≤ 250 ms after true end-of-speech · STT final ≤ 150 ms after endpoint · LLM first token ≤ 350 ms (pre-warmed) · first synthesized audio ≤ 200 ms.
- Budgets are per-turn, measured by the `metric.turn` taps (AR-42) — production telemetry and benchmarks use the *same* instrumentation, so numbers are comparable by construction.
- Changing a budget is a constitutional change (ADR against Part A).

## 2. Resource limits (initial; revised by profile ADR as data arrives)

- **Per-session memory:** ≤ 100 MB steady-state per live session (audio buffers bounded per A8; context bounded by token budget). A leaking session is a Critical defect.
- **Per-worker CPU:** a Session Worker must hold its declared session capacity at <70% sustained CPU on the Stage-2 host class; capacity is *measured*, not asserted.
- **Startup:** Control Plane ready ≤ 5 s; Session Worker ready ≤ 10 s (model warm-up included); session admission → transport-connect-ready ≤ 2 s.

## 3. Streaming & async requirements

- Streaming stages overlap; introducing any wait-for-whole-input step needs an ADR (AR-43).
- **No blocking operations in the event loop** (AR-40). The blocking-operation policy: CPU-bound work → executor pool; sync-only third-party calls → wrapped and declared in the component spec; anything >10 ms on the loop found by profiling is a defect.
- Every external await has a timeout (A10); every queue is bounded (A8).

## 4. Monitoring & profiling

- The per-turn metric event is the primary performance instrument; percentiles per agent/org/provider are retained (Constitution A15-equivalent: observability section).
- Each release records a **benchmark baseline** (scripted conversation set on the reference host) into `docs/performance/` — date, environment, method, numbers (AI_AGENT_RULES #30: unreproducible numbers are anecdotes).
- Profiling (CPU/alloc) runs when: a budget is breached, before each stage promotion, and after substrate/provider swaps.

## 5. Benchmarking & regression workflow

1. Pre-merge (Stage 2+): conversation-loop changes run the latency benchmark; compare against the last baseline.
2. Regression >10% on any stage p50, or any SLO breach → merge blocked.
3. An intentionally accepted regression (e.g., quality-for-latency trade) requires owner approval + DECISION_LOG entry + changelog note — and the budget still holds; if it can't, that's an ADR.
4. Optimization workflow: measure → hypothesize → change one thing → re-measure → record. Optimizations without before/after numbers are rejected in review.

## 6. Cost as performance

Provider cost per session is tracked by the cost meter (Constitution observability section). A change that doubles per-session cost is treated like a latency regression: visible, reviewed, gated.
