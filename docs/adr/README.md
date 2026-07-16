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
