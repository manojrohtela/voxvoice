# ARCHITECTURE_RULES.md — Enforceable Architecture Rules

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 — these rules *restate the Constitution in enforceable form*; on any discrepancy the Constitution wins and this file is corrected. Each rule cites its source. Violations are Severity-High defects (GOVERNANCE §11); rules marked **[CI]** are mechanically enforced.

## Ownership & state

- **AR-1 — One owner per state.** Every piece of state has exactly one writing component; all others read via events or queries. (A2.5)
- **AR-2 — One producer per event type.** Each event type in the A5 vocabulary is emitted by exactly one component. New producers for an existing type require an ADR. (A5)
- **AR-3 — Shared mutable state is prohibited** across tasks/components except inside a single owning actor. (A2.5, A11.1.7)
- **AR-4 — No cross-request in-process state.** Anything that must survive a request or be visible to another worker lives in the System of Record or on the Event Bus — from Stage 1. **[CI-reviewable]** (A9)
- **AR-5 — The Conversation Orchestrator is the sole writer of conversation state**; no component mutates session/turn state directly. (A4.2)
- **AR-6 — Session status must reflect call reality.** Any component learning of a real-world lifecycle fact (hangup, connect, failure) must emit the event that drives the state machine — status may never be inferred-only from API calls. (A4.4; Phase-1 defect M9)

## Layering & imports

- **AR-10 — `core/` imports nothing above it.** **[CI]** (A4.1)
- **AR-11 — `providers/` imports only `core/`.** Never `runtime/`, never `api/`. **[CI]** (A4.1)
- **AR-12 — Vendor SDKs are imported only inside that vendor's Provider Plugin.** **[CI]** (A6.6)
- **AR-13 — The Pipeline Substrate is imported only by `runtime/pipeline/`.** **[CI]** (D2)
- **AR-14 — `api/` reaches `runtime/` only via the session-control interface and the Event Bus** — no direct imports of runtime internals. **[CI]** (A4.1)
- **AR-15 — No circular dependencies** at module or package level. **[CI]** (A11.1.7; Phase-1 defect §9.1)

## Runtime & providers

- **AR-20 — The runtime owns the conversation; providers own exactly one capability each.** No conversation logic (turn policy, memory, prompts, scheduling) inside a plugin. (A6)
- **AR-21 — No provider-specific business logic outside provider modules.** No `if vendor == …` anywhere in `runtime/`, `api/`, or `core/`. (A6.6)
- **AR-22 — Capability flags are declared by plugins and validated at configuration write time**; an agent config the plugins can't satisfy is rejected, loudly. (A6.1)
- **AR-23 — Every provider call passes through the fault-tolerance layer** — timeout, typed error, circuit breaker, optional failover chain. Direct raw calls are defects. (A10)
- **AR-24 — Degradations are declared, evented, and recorded** in session metadata — never silent. (A6.4)
- **AR-25 — Accepted config must act** — every accepted field is honoured by the runtime or the write is rejected. Both directions: no dead config, no hidden tunables. (A2.6)

## Events & interfaces

- **AR-30 — Every runtime fact is an event in the standard envelope**, ordered per-session (`seq`); components integrate across layers via events, not direct calls. (D4, A5)
- **AR-31 — Producers never block on consumers**; a dead consumer never stalls a conversation. (A5)
- **AR-32 — Event payload schemas are versioned; they evolve additively or by version bump, never by silent mutation.** (A5, A11.4)
- **AR-33 — Every public interface is versioned from first release** with the deprecation flow of CODING_STANDARDS §10. (A11.4)

## Media & performance

- **AR-40 — No blocking I/O in the event loop; no unbounded queues in media paths** (bounded ring buffers with declared overflow policy). **[CI-lintable in part]** (A8, A11.1.7)
- **AR-41 — Sample rate is a session property**, converted at exactly one ingress and one egress point — never an ambient assumption. (A8)
- **AR-42 — Every stage boundary in the conversation loop carries a latency tap** feeding `metric.turn`; a new stage without a tap doesn't merge. (I1, A8)
- **AR-43 — Streaming stages overlap** — introducing a wait-for-whole-input step into the conversation loop requires an ADR (it moves the structural latency floor). (A8)

## Structure & change control

- **AR-50 — Every module has one responsibility** stated in its header docstring; a module needing "and" in its responsibility is split. (A14)
- **AR-51 — Every runtime component has its A11.3 specification** (responsibilities, inputs, outputs, dependencies, lifecycle, events, failure modes, recovery) before merge. (A11.3)
- **AR-52 — Extension is additive** (new plugin, new event type, new capability contract, new stage-table row). A feature that can't be expressed additively is a constitutional amendment → ADR against Part A. (A12)
- **AR-53 — Architecture over convenience.** "It was faster this way" is not a defence; the compliant design is the default, and any exception is a logged, owner-approved decision with a repayment date. (GOVERNANCE §2)
- **AR-54 — Deleted architecture leaves a documentation trail** — removed integrations/components are archived in `docs/research/` or marked superseded, never silently erased. (D7, A13)
