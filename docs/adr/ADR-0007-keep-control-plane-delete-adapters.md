# ADR-0007 — Keep and harden control-plane assets; delete the engine-adapter axis

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution Part A — foundational decision D7 |
| **Deciders** | Technical Owner |
| **Related** | ADR-0001 (first-party runtime), ADR-0002 (substrate), [Phase 1 audit](../audits/phase-1-repository-audit.md) |

## Problem

Given the pivot to a first-party runtime (ADR-0001), decide precisely what in the Phase 1 prototype is kept, hardened, rewritten, or deleted — without discarding genuinely good, audit-verified assets or preserving the wrong abstraction out of sunk cost.

## Context

The Phase 1 audit rated the control-plane code well-built (clean auth, org-scoping discipline with tests, signed webhooks, schema/model separation) but found the whole-engine `EngineAdapter` abstracts the wrong axis for the vision: it hides exactly the runtime surfaces (events, control, metrics) the product must expose.

## Alternatives considered

- **(a) Keep everything, add the runtime alongside.** Carries a dead abstraction that every runtime feature must route around. Rejected.
- **(b) Rewrite everything from scratch.** Discards verified, working auth/scoping/webhook assets and their tests. Rejected (wasteful, reintroduces solved bugs).
- **(c) Keep-and-harden the control-plane assets; delete the engine-adapter axis; rewrite the prototype voicebot into the Runtime Plane seed.** **Selected.**

## Selected solution

- **Keep (hardened):** FastAPI app shape, data models (extended), org/API-key auth with hashing, org-scoping discipline + its tests, webhook HMAC signing, schema/model separation, settings pattern, test-with-fakes approach.
- **Delete (at migration M1):** `platform/adapters/` (Dograh + LiveKit + factory), the `/v1/engine/dograh/events` router, the transcript poller, `Agent.engine_workflow_id`, and the webclient's embed-script mounting. Dograh knowledge is archived first (see below).
- **Rewrite:** the voicebot becomes the first Session Worker (migration M2), not a sidecar demo.

## Trade-offs

A brief window at M1 where session-start is unavailable (honest 503) until the Runtime Plane seed lands at M2. Accepted — clean deletion beats carrying a dead axis.

## Risks

Losing verified integration knowledge on deletion — mitigated by archiving it to [`docs/research/dograh-api-notes.md`](../research/dograh-api-notes.md) **before** M1 (done at M0, per ARCHITECTURE_RULES AR-54). Regression in kept assets during hardening — mitigated by retaining and extending their existing tests.

## Future impact

The replaceable axis becomes capability-level Provider Plugins (A6), not whole engines. Removed integrations leave a documentation trail, never a silent gap. Supersedes SPEC.md §5 (engine-adapter architecture) and §8 (repo layout → Constitution §A14).

> **M0 scope note:** this ADR *records* the delete/keep decision; the deletions themselves execute at migration **M1**. At M0 the current code (including `adapters/`) is committed as the baseline so M1's changes are tracked and reversible.
