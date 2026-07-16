# ADR-0001 — VoxVoice builds and owns a first-party voice runtime

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D1 |
| **Deciders** | Technical Owner |
| **Sources** | [Phase 1.5 voice-runtime audit](../audits/phase-1.5-voice-runtime-audit.md), [Constitution v2 §A3 D1](../architecture/engineering-constitution-v2.md) |

## Problem

Phase 1 bet the product on renting a complete external voice engine (Dograh) behind an `EngineAdapter`. The product vision states *the runtime is the product*. You cannot rent your product and still differentiate on it.

## Context

The Phase 1.5 audit proved the rented path is **blind**: no live events, no mid-call control (could not even end a call), transcripts available only *after* the call via polling, no per-stage latency, and conversation progression governed by an external engine's LLM-judged edge conditions. Renting forfeits exactly the surfaces (I1 latency, I2 observability/control) that define the product.

## Alternatives considered

- **(a) Deepen the Dograh integration.** Rejected: capabilities unverified, ceiling owned by a third party, differentiation impossible, live events/control may not exist in its API at all.
- **(b) Adopt a full agents framework with its own media infra as the runtime.** Viable substrate but heavier infrastructure than the current stage needs, and still a framework to wrap rather than a product to own.
- **(c) Speech-to-speech realtime model as the core.** Rejected as the core: provider lock-in is the opposite of the vision; target-language (Indic) support immature. Revisit later as a *provider/pipeline type* behind the same session/event contract (Constitution A12).
- **(d) Build and own a first-party runtime** on a confined commodity substrate (ADR-0002). **Selected.**

## Selected solution

VoxVoice builds and owns its conversation runtime (turn-taking, interruption, memory, timing, events, control). External full-stack engines may exist only as optional, clearly capability-degraded integration plugins — never the core path. The whole-engine `EngineAdapter` axis is retired; the replaceable axis becomes capability-level Provider Plugins (ADR-0007, ADR-0002).

## Trade-offs

We take on turn-taking, interruption, memory, and concurrency engineering (estimated 8–12 focused weeks, audit F14). In return: full event/control/metric access, provider integration on our terms, and a product that is actually ours. Commodity layers (codecs, media plumbing, model inference) are still rented, behind contracts.

## Risks

Solo-developer scope is the main risk. Mitigated by staged delivery (Constitution migration M0–M6) and by the substrate (ADR-0002) carrying commodity work. Secondary risk: reinventing solved problems — mitigated by extracting contracts from the first working implementation rather than designing them speculatively.

## Future impact

Every runtime capability (streaming STT, turn intelligence, mid-call control, live events) becomes buildable and tunable in-house. The Dograh adapter and its verified API knowledge are archived to [`docs/research/dograh-api-notes.md`](../research/dograh-api-notes.md); the code is deleted at migration M1 (ADR-0007). Supersedes SPEC.md §10.1 (Dograh-primary).
