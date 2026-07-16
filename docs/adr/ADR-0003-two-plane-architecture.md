# ADR-0003 — Two-plane architecture (Control Plane / Runtime Plane)

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D3 |
| **Deciders** | Technical Owner |
| **Related** | ADR-0004 (events), ADR-0006 (System of Record) |

## Problem

Phase 1 mixed control concerns (auth, CRUD) and runtime concerns (transcript pollers, adapter singletons) in one process, producing single-process traps: in-process state that silently forbids a second worker (Phase 1 audit H4).

## Context

Control work is stateless request/response and scales horizontally trivially. Runtime work is stateful, long-lived, and latency-sensitive (live sessions). Binding them in one process couples their scaling and failure domains.

## Alternatives considered

- **(a) Single monolith process.** Simplest; repeats H4; couples scaling. Rejected.
- **(b) Microservice per component.** Absurd operational overhead for the current stage. Rejected.
- **(c) Two planes — a stateless Control Plane and a stateful Runtime Plane** that share nothing in memory and communicate via the System of Record and the Event Bus. **Selected.** Co-located in one process during development; separate processes/hosts in production **without code change**.

## Selected solution

Control Plane (`api/`): auth, configuration, session admission, control commands, event delivery to consumers. Runtime Plane (`runtime/`): Session Workers executing live conversations. No shared memory; all cross-plane communication via System of Record + Event Bus.

## Trade-offs

A session→worker routing indirection exists from day one, even with a single worker. That is the deliberate price of never rewriting for scale.

## Risks

Slight early complexity for a solo developer. Mitigated: both planes run in one process at Stage 1 (`make dev`), so the split is logical before it is physical.

## Future impact

Horizontal scaling = add Runtime Plane workers; the Control Plane stays stateless. Enables the Constitution A9 scale stages with no component-boundary changes. Enforced by ARCHITECTURE_RULES AR-4, AR-14.
