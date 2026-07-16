# TESTING_POLICY.md — Testing Philosophy & Requirements

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · **Enforces:** Constitution A11.5 (quality gates), A6.3 (plugin contract suites).

## Philosophy

Tests exist to make change safe, not to hit a number. We test **behaviour and contracts**, not implementation details; we use **fakes for every capability contract** so the full conversation loop runs in CI with zero vendor calls; we treat determinism as a feature under test (the state machine is specified by its transition table and the tests assert that table). A feature's tests land in the same change-set as the feature — always (DEFINITION_OF_DONE).

## Test taxonomy — what and when

| Type | Verifies | Required when | Runs |
|---|---|---|---|
| **Unit** | one component's logic in isolation (fakes for everything external) | every new/changed behaviour | every push (fast, <60 s total) |
| **Contract (capability)** | a Provider Plugin satisfies its capability contract — streaming semantics, partials, error typing, timeout behaviour, capability flags truthfulness | every new plugin; every contract version change; every plugin change | every push (against recorded/mock vendor responses); nightly against live vendors where keys exist |
| **Integration** | components wired together: orchestrator + turn intelligence + memory over the Event Bus, API + System of Record | any change crossing a component boundary | every push |
| **State-machine** | exhaustive transition-table coverage: every legal transition asserted, every illegal transition raises, timeout policies fire | any orchestrator/lifecycle change | every push |
| **End-to-end (with fakes)** | full session: start → scripted "conversation" through fake STT/LLM/TTS → events → transcript → end; reconnect and barge-in scripts included | any runtime-path change | every push |
| **Runtime tests (live-ish)** | real transport + real providers on a deployed instance; one scripted spoken call | before every release; after provider/transport changes | pre-release, on the Stage-2 host |
| **Latency** | per-stage budgets and voice-to-voice SLO (A8) via the `metric.turn` taps under scripted load | any conversation-loop change | pre-merge benchmark from Stage 2 (PERFORMANCE_POLICY); trend tracked per release |
| **Regression** | every fixed defect gets a test that fails on the old code | every bug fix — no fix merges without its regression test | every push |
| **Conversation quality** | turn-detection accuracy (thinking-pause survival, endpoint latency), interruption behaviour (backchannel tolerance, cancel latency, context truncation correctness), language evals (`evals/` phrase sets incl. the mandated Hinglish set) | any Turn Intelligence, interruption, STT/TTS plugin, or prompt-runtime change | scheduled + pre-release; scores recorded in `docs/performance/` |
| **Memory tests** | token-budget enforcement, summary correctness (facts survive compression), entity persistence, snapshot/restore across reconnect and worker restart | any Memory Engine change | every push (unit/integration level) + e2e reconnect script |
| **Load** | N concurrent sessions holding latency SLOs; worker backpressure; no cross-session leakage | before Stage-2 pilot; before each stage promotion; after concurrency-relevant changes | scheduled on deploy target (50-session baseline per Constitution profile roadmap M6) |

## Rules

1. **No real vendor calls in unit/integration CI.** Vendor interaction is confined to contract (recorded/nightly) and runtime/pre-release tiers.
2. **No sleeps for synchronization** — virtual time or event awaiting. A test that needs a sleep has found a design defect.
3. **Failing tests are never skipped, loosened, or deleted to go green** (AI_AGENT_RULES #16). Flaky tests: quarantined ≤1 day, fixed ≤1 week, else the covered feature is treated as untested (GOVERNANCE §11).
4. **Cross-org isolation is permanently under test** — the Phase-1 org-scoping suite is retained and extended to every new resource type; any new query without an org-scope test does not merge.
5. **Every event type has at least one test asserting its envelope, producer, and payload schema version.**
6. **Fakes are first-class code:** they live with the contracts, implement the full capability interface, and are kept behaviourally honest (a fake that diverges from vendor reality gets fixed the day the divergence is found — log it in DECISION_LOG).
7. Coverage is reviewed as *behaviour coverage* in review (REVIEW_CHECKLIST §6), not as a percentage gate; a number can be gamed, a checklist answer cannot.
