# ADR-0004 — Event-driven core with one typed event envelope

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D4 |
| **Deciders** | Technical Owner |
| **Related** | ADR-0003 (planes), ADR-0006 (System of Record) |

## Problem

Phase 1.5 §11: no live events, the promised SDK `state` callback was unimplementable, and components were coupled by direct callbacks (`on_transcript`) with no replay or audit trail.

## Context

A voice conversation is intrinsically a stream of facts (speech started, transcript final, thinking, speaking, interrupted, tool called). Modelling those as first-class events serves live clients, consumer webhooks, persistence, metrics, and debugging simultaneously.

## Alternatives considered

- **(a) Direct callback wiring between components.** N×M coupling, no replay, no audit trail, no fan-out. Rejected (it is what Phase 1 had).
- **(b) Events for external delivery only, direct calls internally.** Two integration models to maintain; internal coupling remains. Rejected.
- **(c) Fully event-driven core: every runtime fact is one typed, enveloped, ordered event**, produced once by its owner, fanned out to all consumers. **Selected.**

## Selected solution

One envelope for every event (`id, session_id, org_id, type, ts, seq, turn, data`), strict per-session ordering via `seq`, one producer per event type. Consumers: client channel (live), consumer webhooks (durable outbox + retry + dead-letter), persistence (`event_log` — the replayable source of session history; transcripts and metrics are projections), metrics pipeline. Producers never block on consumers.

## Trade-offs

Slight indirection and eventual consistency for read models. Accepted — conversations are event-shaped, and the benefits (fan-out, replay, audit) are exactly what the product needs.

## Risks

Event schema churn — mitigated by per-event-type payload versioning (Constitution A11.4, AR-32). Outbox/queue throughput ceilings — deferred to the scale stage that needs a broker (ADR-0006).

## Future impact

Analytics, billing/metering, session replay/debugging, and multi-region fan-out all become event consumers added without touching the runtime. Enforced by ARCHITECTURE_RULES AR-2, AR-30, AR-31, AR-32.
