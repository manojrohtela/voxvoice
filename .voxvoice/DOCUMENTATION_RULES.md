# DOCUMENTATION_RULES.md — Documentation Requirements

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · **Builds on:** Constitution A13 (documentation constitution) and [`docs/README.md`](../docs/README.md) (conventions). This document adds the operating rules; where the three overlap, Constitution A13 wins.

## 1. Structure & required documents

- The documentation tree is Constitution A13 / `docs/README.md`: `INDEX.md` (central index) plus `architecture/ · adr/ · audits/ · runtime/ · apis/ · providers/ · performance/ · security/ · testing/ · deployment/ · roadmap/ · research/`. Sections open with their first document.
- **Required at all times:** `docs/INDEX.md` (current) · the ratified Constitution · this governance system (`.voxvoice/`).
- **Required per artifact:** every runtime component → an A11.3 spec in `docs/runtime/` · every public interface → reference in `docs/apis/` (REST, event catalogue, webhooks, SDK) · every provider plugin → integration notes + capability matrix in `docs/providers/` · every release → release notes + changelog entry.
- A document that isn't linked from `INDEX.md` doesn't exist (docs/README.md rule 1).

## 2. Update policy (synchronization)

- **Same-change-set rule:** any change affecting architecture, runtime behaviour, configuration, or a public interface includes its documentation update in the same commit/PR. The change is incomplete without it (GOVERNANCE §6; workflow Phases 5 & 10).
- Docs are written **before** implementation (intended state, Phase 5) and **re-verified after** (actual state, Phase 10).
- Obsolete documents are marked **Superseded** with a pointer to their replacement — never deleted, never silently rewritten (audits and accepted ADRs are immutable).
- Undocumented behaviour is unspecified behaviour: it may be changed or removed without deprecation.

## 3. ADR workflow

1. **Trigger:** any architectural decision or change (ARCHITECTURE_RULES; GOVERNANCE §7) — including decisions *not* to do something significant.
2. **Type:** constitutional ADR (amends Part A; full template: Problem, Context, Alternatives Considered, Selected Solution, Trade-offs, Risks, Future Impact) or profile ADR (amends Part B; lightweight: row, reason, migration note).
3. **Numbering:** `ADR-NNNN-slug.md` / `P-ADR-NNNN-slug.md`, sequential, never reused.
4. **Lifecycle:** `Proposed → Accepted → (Superseded by ADR-XXXX)`. Accepted ADRs are immutable; to change course, write a superseding ADR.
5. **Approval:** constitutional ADRs require owner approval; profile ADRs require review per REVIEW_CHECKLIST.
6. **Linkage:** every ADR is linked from `docs/INDEX.md` and cross-referenced from the documents it affects; affected docs get a revision note.
7. **Timing:** the ADR is Accepted **before** implementation begins (workflow Phase 3).

## 4. Diagrams

- Every architecture/runtime document with more than three interacting parts includes a diagram (ASCII or committed image source). Diagrams show *responsibilities and flow*, not vendor names (Part A neutrality).
- A diagram contradicting the text is a defect in the document; fix both in one change.

## 5. Architecture & runtime documentation

- `docs/architecture/`: the Constitution + focused designs (event system, memory engine, …), each following Part A / Part B separation.
- `docs/runtime/`: one A11.3 component spec per runtime component; the state-machine transition tables live here and are the reference the tests assert against.

## 6. API documentation

- `docs/apis/` holds: REST/control API reference, the **event catalogue** (every A5 event type with versioned payload schema, producer, consumers), webhook delivery guide (signing, retries, idempotency), SDK usage. Interface docs state their version and deprecation status (A11.4).

## 7. Release notes & changelog

- `CHANGELOG.md` at repo root, Keep-a-Changelog style, one entry per merged change under `Unreleased`, rolled into a version section at release (GIT_WORKFLOW §5).
- Release notes (per release, in `docs/roadmap/` or the tag description) summarize consumer-visible changes, deprecations, migration steps, and known issues.

## 8. Document ownership & version history

- Every document carries a status header: status (Draft / Active / Final / Superseded), version, date, owner. Default owner is the Technical Owner until roles exist; ownership transfers are logged in DECISION_LOG.
- Version history: material revisions bump the version and either append a revision record (living docs) or create a new versioned file (immutable docs — audits, constitutions, ADRs). Git history complements but does not replace status headers.
