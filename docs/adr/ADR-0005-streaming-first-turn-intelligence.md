# ADR-0005 — Streaming-first ingestion with a dedicated Turn Intelligence layer

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D5 |
| **Deciders** | Technical Owner |
| **Sources** | [Phase 1.5 audit §2, §4](../audits/phase-1.5-voice-runtime-audit.md) |

## Problem

Phase 1.5 §2/§4: the prototype used batch speech-to-text (whole utterance transcribed only after a fixed 600 ms silence) plus a fixed silence timer for endpointing. This structurally caps latency (~1.3–2.5 s voice-to-voice) and makes conversations feel robotic — you cannot tune your way out; the STT stage must be re-architected.

## Context

Product invariant I1 requires voice-to-voice p50 ≤ 1.0 s. Natural turn-taking requires distinguishing thinking pauses from turn completion — impossible with a silence timer alone. Both need streaming partial transcripts flowing *during* the caller's speech.

## Alternatives considered

- **(a) Keep batch STT, tune the silence timer.** Cannot cross the structural latency floor; thinking-pauses still steal turns. Rejected.
- **(b) Streaming STT, silence-only endpointing.** Better latency, still no semantic turn detection. Insufficient.
- **(c) Streaming STT (mandatory for first-class STT) + a dedicated Turn Intelligence layer** fusing activity detection, semantic end-of-turn scoring, and adaptive delay; interruption as a policy engine. Batch-only providers allowed only in *declared degraded mode*. **Selected.**

## Selected solution

First-class Speech-to-Text capability requires streaming partial hypotheses (capability flag, ADR-0007/A6). Endpointing = activity detection + semantic end-of-turn score + adaptive delay honouring per-agent `min/max_endpointing_delay`. Interruption is a policy engine over activity-detection triggers (min-words, backchannel tolerance, confidence gates). On high end-of-turn probability the LLM call is pre-warmed so first-token latency lands inside the endpoint delay.

## Trade-offs

Narrows the first-class STT vendor list to streaming-capable providers; batch/whisper-class become degraded-mode fallbacks. Turn Intelligence is the single hardest component we own (audit F14, High). Accepted — this layer plus target-language quality is the moat.

## Risks

Turn-detection quality is empirical and needs ongoing tuning and evals. Mitigated by the conversation-quality test tier (TESTING_POLICY) and eval phrase sets. Streaming vendor availability for target languages must be verified (see P-ADR-0001).

## Future impact

Enables competitive latency and natural turn-taking — exactly what no rented engine would let us tune. Enforced by ARCHITECTURE_RULES AR-42, AR-43 and the latency budgets in Constitution A8 / PERFORMANCE_POLICY.
