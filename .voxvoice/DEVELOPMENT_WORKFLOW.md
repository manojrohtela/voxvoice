# DEVELOPMENT_WORKFLOW.md — Mandatory Implementation Workflow

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3

Every task — feature, fix, refactor, dependency bump — moves through these phases **in order**. No phase may be skipped; phases may be *sized* to the task (a typo fix passes through most phases in seconds, but passes through them). The current phase must be identifiable at any time (AI sessions state it; humans note it in the PR).

```
1 Requirement → 2 Impact Analysis → 3 Architecture Validation → 4 Implementation Plan
→ 5 Documentation Update → 6 Implementation → 7 Testing → 8 Performance Validation
→ 9 Security Validation → 10 Documentation Synchronization → 11 Commit → 12 Review → 13 Merge
```

## Phase definitions & exit criteria

**1. Requirement.** State what is being built and why, in consumer/caller terms where possible. *Exit:* a one-paragraph requirement with acceptance criteria. Ambiguity → ask, don't guess (AI_AGENT_RULES #2).

**2. Impact Analysis.** Identify affected components, events, interfaces, configuration, documents, and tests. Check DECISION_LOG and existing ADRs for prior decisions on this ground. *Exit:* written list of touched artifacts, including docs that will need updating.

**3. Architecture Validation.** Map the design onto the Constitution: which Part A sections govern it, which ARCHITECTURE_RULES apply, whether any layer boundary, event ownership, or state ownership changes. *Exit:* either "compliant, sections cited" or "requires ADR" — in the latter case the ADR is written and accepted **before** Phase 4. Conflict with governance → stop per GOVERNANCE §3.

**4. Implementation Plan.** Ordered steps, each leaving the system working; test plan per TESTING_POLICY; rollout/rollback notes if behaviour changes. *Exit:* plan recorded (PR description or design doc for large work).

**5. Documentation Update (design-level).** Write or update the affected specs *first*: component specs (A11.3), interface/version notes (A11.4), event catalogue entries, ADR links. Writing docs first exposes design holes before code hardens them. *Exit:* docs describe the intended end state.

**6. Implementation.** Code per CODING_STANDARDS and ARCHITECTURE_RULES, smallest correct increments, events + metrics + config validation included as built (not appended later). *Exit:* working code, no TODOs, no warnings.

**7. Testing.** Execute the Phase-4 test plan: new tests written, full affected suites run, contract suites for any touched capability. *Exit:* all green, coverage of the new behaviour demonstrated (TESTING_POLICY).

**8. Performance Validation.** For runtime-path changes: run the latency benchmark/taps and compare against budgets (PERFORMANCE_POLICY; Constitution A8). For non-runtime changes: state "no runtime path touched" explicitly. *Exit:* numbers recorded, budgets met, or a logged decision for any accepted regression (rare, owner-approved).

**9. Security Validation.** Check against SECURITY_POLICY: input validation on new surfaces, authz scoping on new queries, no secret exposure, dependency risk of new packages. *Exit:* checklist pass noted.

**10. Documentation Synchronization.** Re-verify the Phase-5 docs against what was *actually* built; update INDEX.md links, changelog entry, `.env.example` for new config. *Exit:* docs match reality; a reader could operate the feature from docs alone.

**11. Commit.** Per GIT_WORKFLOW: small, atomic, conventional message referencing the task; validation output real, not assumed (AI_AGENT_RULES #9).

**12. Review.** Against REVIEW_CHECKLIST.md, every dimension. Solo/AI work: written self-review recorded. Architecture violations block (GOVERNANCE §9).

**13. Merge.** Only when DEFINITION_OF_DONE.md is fully satisfied. Merge = the feature is *operable*, not merely written.

## Special cases

- **Hotfixes** (production down): phases compress but still execute — the impact analysis may be three sentences and review may follow within 24 h (GIT_WORKFLOW §hotfix), but Phases 7 and 9 are never waived.
- **Spikes/prototypes:** live on `spike/*` branches, exempt from Phases 5–10, and are **never merged** — learnings are extracted into docs and the spike is deleted (prevents "prototype promotion" drift).
- **Documentation-only changes:** Phases 6–9 collapse to consistency checks; Phase 12 still applies.
- **Reverts:** fast-path (Phases 1–3 in one note), because restoring a known-good state is always compliant.
