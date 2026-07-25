# Architecture Decision Records (ADR)

ADRs are the second-highest authority in VoxVoice (below the Engineering
Constitution, above governance) — see [`.voxvoice/README.md`](../../.voxvoice/README.md)
authority hierarchy. Process: [`.voxvoice/DOCUMENTATION_RULES.md`](../../.voxvoice/DOCUMENTATION_RULES.md) §3.

## Kinds

- **Constitutional ADR** (`ADR-NNNN-slug.md`) — amends Constitution **Part A** (timeless
  architecture). Full template: Problem · Context · Alternatives Considered ·
  Selected Solution · Trade-offs · Risks · Future Impact. Owner approval required.
- **Profile ADR** (`P-ADR-NNNN-slug.md`) — amends Constitution **Part B** (Implementation
  Profile — today's vendors/frameworks). Lightweight: what row changes, why, migration note.
- **Epic 6 Agent Platform ADR** (`ADR-NNNN-slug.md`, same numbering sequence as
  Constitutional ADRs — no separate prefix) — records a load-bearing architectural
  decision within the Epic 6 Agent Platform (`docs/architecture/epic6/`) that does
  not itself amend Constitution Part A or B. Template: Status · Context · Decision ·
  Consequences · Alternatives Considered (per the M6.6.5 Documentation & ADR Closure
  milestone). Introduced starting at ADR-0008; the two existing kinds above predate
  Epic 6 and did not anticipate capability-internal architecture decisions of this
  kind, which is why this is recorded as a third kind rather than forced into either.

## Rules
- Numbers are sequential and never reused. Status: `Proposed → Accepted → Superseded by ADR-XXXX`.
- Accepted ADRs are **immutable**; to change course, write a superseding ADR (never edit).
- Every ADR is linked from [`docs/INDEX.md`](../INDEX.md) and cross-referenced from the docs it affects.

## Register

| ADR | Title | Amends | Status |
|---|---|---|---|
| [ADR-0001](ADR-0001-first-party-runtime.md) | First-party voice runtime; retire whole-engine adapter axis | Part A (D1) | Accepted 2026-07-16 |
| [ADR-0002](ADR-0002-substrate-behind-contracts.md) | Pipeline substrate confined behind owned contracts | Part A (D2) | Accepted 2026-07-16 |
| [ADR-0003](ADR-0003-two-plane-architecture.md) | Two-plane architecture (Control / Runtime) | Part A (D3) | Accepted 2026-07-16 |
| [ADR-0004](ADR-0004-event-driven-core.md) | Event-driven core with one typed envelope | Part A (D4) | Accepted 2026-07-16 |
| [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md) | Streaming-first ingestion + Turn Intelligence layer | Part A (D5) | Accepted 2026-07-16 |
| [ADR-0006](ADR-0006-single-system-of-record.md) | Single System of Record; brokers only when needed | Part A (D6) | Accepted 2026-07-16 |
| [ADR-0007](ADR-0007-keep-control-plane-delete-adapters.md) | Keep control-plane assets; delete engine-adapter axis | Part A (D7) | Accepted 2026-07-16 |
| [P-ADR-0001](P-ADR-0001-implementation-profile-v1.md) | Adopt Implementation Profile v1 | Part B | Accepted 2026-07-16 |
| [ADR-0008](ADR-0008-skill-manifest-not-code.md) | Skills are a versioned manifest, not code | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0009](ADR-0009-workflow-durable-repository-exception.md) | Workflow execution state is the platform's one durable-repository exception | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0010](ADR-0010-hook-framework-cross-cutting-library.md) | The Hook Framework is a cross-cutting library, not a `core.ports` runtime | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| ADR-0011 | MCP/A2A are bridge-pattern adapters, never a `ToolPort`/`AgentPort` redesign | Epic 6 Agent Platform Architecture | Reserved — not yet ratified (MCP/A2A not yet designed) |
| [ADR-0012](ADR-0012-tool-contract-manifest-plus-adapter.md) | Tool contract is manifest + registered adapter, never manifest-supplied code | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0013](ADR-0013-dual-fsm-goal-scoped-for-agent.md) | Dual-FSM closed-runtime pattern; Agent's second FSM attaches to the Goal, never the identity | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0014](ADR-0014-structure-intelligence-correctness-split.md) | Structure, intelligence, and correctness are distinct ownership categories | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0015](ADR-0015-agent-workflow-port-seam.md) | Agent Runtime delegates via its own `AgentWorkflowPort` seam, not literal `WorkflowPort` | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0016](ADR-0016-loop-engine-no-registrable-identity.md) | Loop Engine has no registrable identity | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
| [ADR-0017](ADR-0017-execution-context-duplication-lineage.md) | Execution-context shapes extend by structural duplication within one delegation lineage; Loop Engine is deliberately outside it | Epic 6 Agent Platform Architecture | Accepted 2026-07-25 |
