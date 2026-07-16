# REVIEW_CHECKLIST.md — Mandatory Engineering Review Checklist

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · Every change is reviewed against **all 14 dimensions** before merge (GOVERNANCE §9). Solo/AI work records a written self-review answering each dimension. "N/A" is a valid answer only with a one-line reason.

## 1. Architecture
- [ ] Complies with the Constitution sections cited in the plan (workflow Phase 3); no ADR-requiring change slipped in without one.
- [ ] No ARCHITECTURE_RULES violation: layer imports (AR-10…15), state ownership (AR-1…6), substrate confinement (AR-13).
- [ ] Responsibilities landed in the right module (A14); nothing needs "and" to describe.

## 2. Correctness
- [ ] Does what the requirement says; acceptance criteria demonstrably met.
- [ ] Edge cases handled: empty/None, reconnect, mid-turn failure, concurrent sessions, out-of-order events.
- [ ] State-machine changes match the documented transition table; illegal transitions still raise.

## 3. Maintainability
- [ ] Names from the glossary; no new synonyms; no free-string enums.
- [ ] No duplicated logic (searched before written); no dead code; no commented-out code; no naked TODOs (only `DEFERRED(DL-xxx)`).
- [ ] A newcomer could understand this change from the code + docs alone.

## 4. Security
- [ ] New inputs schema-validated; new queries org-scoped (with tests); new URLs SSRF-checked.
- [ ] No secrets in code/logs/tests/fixtures; constant-time secret comparisons; no new unauthenticated surface.
- [ ] Raw vendor errors don't reach consumers.

## 5. Performance
- [ ] Runtime-path change → benchmark run, numbers in the PR, budgets held (PERFORMANCE_POLICY §5); otherwise "no runtime path touched" stated.
- [ ] No blocking calls on the event loop; every external await has a timeout; every new queue is bounded.
- [ ] No new per-request allocation/connection churn (clients pooled, resources lifecycle-owned).

## 6. Testing
- [ ] New behaviour has tests in this change-set; bug fixes have a regression test that fails on the old code.
- [ ] Right tiers per TESTING_POLICY (unit / contract / state-machine / e2e-with-fakes as applicable).
- [ ] Tests are deterministic (no sleeps), use fakes, and assert behaviour not implementation.

## 7. Documentation
- [ ] Same-change-set docs updated: component spec (A11.3), API/event catalogue, `.env.example`, changelog entry, `INDEX.md` link if a new doc.
- [ ] Docs re-verified against what was *actually* built (Phase 10), not the plan.
- [ ] ADR written/linked if the decision was architectural; superseded docs marked.

## 8. Observability
- [ ] New runtime path emits its events (correct producer per AR-2) and latency taps (AR-42) in this PR.
- [ ] Logs are structured, levelled correctly, carry session/turn/org ids, narrate decisions; nothing sensitive logged.
- [ ] Failure modes are visible: typed `error.*` events, not silent catches.

## 9. Scalability
- [ ] No cross-request in-process state (AR-4); nothing assumes single-worker or single-process.
- [ ] Resource lifecycle correct under N concurrent sessions (per-session isolation, teardown reaps everything).
- [ ] Works unchanged across deployment stages (A9) — or the stage table was updated.

## 10. Provider neutrality
- [ ] No vendor names/SDK imports outside `providers/` (and substrate outside `runtime/pipeline/`) — AR-12/13/21.
- [ ] New provider behaviour expressed through capability flags/contracts, not special cases in runtime.
- [ ] Part B profile table updated if a technology binding changed (with profile ADR).

## 11. Runtime behaviour
- [ ] Conversation behaviour remains deterministic: policy/timing changes are table-driven config, honoured end-to-end (AR-25).
- [ ] Interruption/turn-taking paths still correct: barge-in truncates context to words actually spoken; backchannels don't cancel speech.
- [ ] Session status transitions driven by real events (AR-6); reconnect path unbroken.

## 12. Event consistency
- [ ] Events use the standard envelope, correct `seq`/ordering semantics, one producer per type (AR-2, AR-30).
- [ ] Payload schema changes are additive or versioned (AR-32); event catalogue updated.
- [ ] No producer blocks on a consumer (AR-31).

## 13. Memory impact
- [ ] Context/token budgets still enforced; snapshots still restore correctly after this change.
- [ ] No unbounded growth introduced (buffers, caches, dicts keyed by session/turn without cleanup).
- [ ] Per-session memory stays within PERFORMANCE_POLICY §2 limits.

## 14. Latency impact
- [ ] No new synchronous wait inserted into the conversation loop (AR-43); streaming overlap preserved.
- [ ] Stage budgets (A8) re-checked for every touched stage; `metric.turn` still reports every boundary.
- [ ] Any accepted regression is owner-approved and logged (PERFORMANCE_POLICY §5.3).

**Blocking rule:** dimensions 1 (Architecture), 4 (Security), and any budget breach in 5/14 are hard blocks — comment-and-approve is prohibited (GOVERNANCE §9).
