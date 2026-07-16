# ADR-0006 — Single System of Record; specialized infrastructure only when needed

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D6 |
| **Deciders** | Technical Owner |
| **Related** | ADR-0003 (planes), ADR-0004 (events), P-ADR-0001 (concrete DB/broker) |

## Problem

Decide where all durable state lives — configuration, sessions, transcripts, context snapshots, event log, webhook delivery outbox, metrics — and when (if ever) to introduce specialized infrastructure (brokers, caches, queues).

## Context

The Phase 1 prototype held critical state in process (adapter singletons, de-dupe dicts, pollers), which silently forbade a second worker and risked data loss on restart. At current and near-term scale (Stages 1–3), a single transactional database comfortably carries all durable state, including the event outbox (a standard skip-locked worker pattern).

## Alternatives considered

- **(a) A message broker + cache + queue from day one.** Operational weight with no current need; more moving parts to secure and run. Rejected until a scale stage requires it.
- **(b) In-memory state for speed.** Repeats the Phase 1 data-loss and single-process traps. Rejected.
- **(c) One transactional System of Record for all durable state**, with the live Event Bus as an in-process mechanism now and a distributed broker introduced later **behind the same `EventBus` interface**. **Selected.**

## Selected solution

All durable state lives in a single transactional database per deployment (concrete engine: Part B / P-ADR-0001; an embedded database is acceptable for tests). The webhook outbox and retry queue are rows in it. The live Event Bus is in-process at Stages 1–2; a distributed pub/sub broker swaps in at Stage 3 behind the unchanged `EventBus` interface. Specialized infrastructure enters only at the stage that needs it, always behind an existing interface.

## Trade-offs

Database-as-queue has a throughput ceiling (order thousands of events/second) — far beyond Stage 1–3 needs. The interface seam makes the later broker swap mechanical rather than a rewrite.

## Risks

Under-provisioning at a scale spike — mitigated by the staged plan (A9) and by the `EventBus` seam being present from day one. Cross-request state accidentally kept in process — outlawed by ARCHITECTURE_RULES AR-4 (CI-reviewable).

## Future impact

Backups, audit, replay, and consistency all have one home. Scaling is an infrastructure swap behind stable interfaces, not an architecture change.
