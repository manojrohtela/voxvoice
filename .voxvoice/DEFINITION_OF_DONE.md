# DEFINITION_OF_DONE.md — Feature Completion Checklist

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · A feature/change is **done** only when every applicable item below is checked. AI sessions report against this list item-by-item at task end (AI_AGENT_RULES #24). Partial completion is reported as partial — never as done.

## Implementation
- [ ] Requirement's acceptance criteria met and demonstrated (test output, benchmark, or runnable example — not assertion).
- [ ] All 13 workflow phases traversed (DEVELOPMENT_WORKFLOW); none skipped.
- [ ] No TODOs (only `DEFERRED(DL-xxx)` with logged decisions); no commented-out code; no debug leftovers; no dead code introduced or orphaned.
- [ ] No duplicated logic; existing implementations reused or consciously extended.
- [ ] Configuration added is typed, documented in `.env.example`, and honoured by the runtime (AR-25).

## Build & quality gates
- [ ] Build/imports pass; **zero warnings** (warnings are errors in CI).
- [ ] Lint clean; import-layer rules pass; secret scan clean.
- [ ] Full affected test suites pass — actual output reported, including the contract suites for any touched capability.
- [ ] New behaviour covered by new tests; bug fixes carry their regression test.

## Performance & security
- [ ] Runtime-path changes: benchmark run, per-stage numbers recorded, budgets held (or owner-approved logged exception).
- [ ] Security dimension of REVIEW_CHECKLIST answered; new surfaces validated and scoped.
- [ ] Observability shipped with the change: events, latency taps, structured logs (nothing "to be instrumented later").

## Documentation & governance
- [ ] Same-change-set documentation updated and re-verified against actual behaviour: component spec (A11.3), API/event catalogue, README/runbooks as applicable.
- [ ] ADR written and Accepted if the change was architectural; DECISION_LOG entry for notable non-ADR decisions (new dependency, accepted debt, deferred work).
- [ ] CHANGELOG entry added; `docs/INDEX.md` updated if a document was added/superseded.
- [ ] Part B profile table updated if any technology binding changed.

## Review & merge
- [ ] Reviewed against all 14 REVIEW_CHECKLIST dimensions (written self-review if solo/AI); hard-block dimensions clean.
- [ ] Commits follow GIT_WORKFLOW (atomic, conventional messages); branch up to date with main; merge leaves main releasable.
- [ ] Rollback path known: migrations reversible/expand-contract; feature can be disabled or reverted cleanly.

**The one-line test:** *someone who is not the author could operate, debug, and extend this feature using only the repository.* If not — it isn't done.
