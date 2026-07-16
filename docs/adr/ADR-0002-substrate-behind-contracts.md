# ADR-0002 — Pipeline substrate confined behind VoxVoice-owned contracts

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D2 |
| **Deciders** | Technical Owner |
| **Related** | ADR-0001 (first-party runtime); P-ADR-0001 (current substrate selection) |

## Problem

Owning the runtime (ADR-0001) does not require building commodity media plumbing (transports, resampling, activity-detection wiring, provider streaming) from scratch. Decide whether to stand on a third-party pipeline substrate and, if so, how to keep it from becoming an architectural dependency.

## Context

The differentiation is in the conversation loop, not the frame plumbing. A mature media-pipeline library removes months of undifferentiated work. The danger is coupling: a substrate imported throughout the codebase becomes impossible to replace and dictates our contracts.

## Alternatives considered

- **(a) Build the pipeline from scratch.** Maximal control, months of commodity work for zero differentiation. Rejected (violates KISS).
- **(b) Adopt a substrate but let it permeate the runtime.** Fast today, unbounded blast radius later. Rejected.
- **(c) A lower-level systems language for the core.** Better raw throughput; abandons the developer's leverage and the voice-AI ecosystem. Rejected — network and model inference dominate voice latency, not language runtime. Revisit only if profiling proves otherwise.
- **(d) Adopt a substrate, confined to a single module behind VoxVoice-owned interfaces.** **Selected.**

## Selected solution

The runtime *may* use a third-party pipeline substrate, but it is confined to a single module (`runtime/pipeline/`) behind VoxVoice-owned interfaces (Transport, capability providers, turn hooks). **No other module imports the substrate** (ARCHITECTURE_RULES AR-13, CI-enforced). The concrete substrate is an Implementation-Profile choice (Part B / P-ADR-0001), not architecture.

## Trade-offs

A thin wrapper layer costs some indirection and inherits the substrate's release cadence. Accepted: the wrapper is what makes the substrate replaceable and keeps our provider contracts ours.

## Risks

Substrate breaking changes propagate into one module. Wrong early abstractions if contracts are designed before the first working plugin — mitigated by extracting contracts from the first implementation (M2/M3) then freezing.

## Future impact

If the substrate is ever outgrown or abandoned, the blast radius is one directory. Substrate replacement is a Profile ADR, not a constitutional change.
