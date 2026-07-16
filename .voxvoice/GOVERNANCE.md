# GOVERNANCE.md — Supreme Engineering Policy

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 (below Constitution & ADRs; above all other artifacts)

## 1. Project philosophy

VoxVoice is a commercial Voice AI Runtime Platform built to last 5–10 years under continuous change of vendors, frameworks, and contributors. The [Engineering Constitution](../docs/architecture/engineering-constitution-v2.md) Part A is the architecture; everything else — including this document — exists to keep implementations inside it. Engineering quality outranks delivery speed; delivery speed outranks feature count. We ship small, measured, documented increments.

## 2. Engineering priorities (ordered; ties break upward)

1. **Correctness of conversation behaviour** (deterministic state machine, no silent config)
2. **Latency** (Constitution I1 — budgets are release gates)
3. **Observability** (I2 — unmeasured code is unfinished code)
4. **Security & data protection**
5. **Maintainability & provider neutrality** (I3)
6. **Feature throughput**

A change that improves a lower priority at the cost of a higher one is rejected by default and requires an explicit, logged decision.

## 3. Relationship with the Engineering Constitution

- The Constitution defines architecture (Part A) and current technology bindings (Part B). This governance system defines working process. Governance can never amend architecture; only ADRs can.
- Every rule in [ARCHITECTURE_RULES.md](ARCHITECTURE_RULES.md) is a restatement of the Constitution in enforceable form; if a discrepancy is found, the Constitution wins and the rule file is corrected.
- The authority hierarchy in [README.md](README.md) is binding. **If any implementation conflicts with this document or anything above it, implementation stops until the conflict is resolved at the right level.**

## 4. Mandatory engineering rules (summary; detail in the per-topic documents)

1. Every task follows the [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) phases — no phase skipped.
2. Every merge satisfies [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md) and [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md).
3. Every architectural decision has an ADR **before** implementation ([DOCUMENTATION_RULES.md](DOCUMENTATION_RULES.md) §ADR).
4. Every configuration field is enforced or rejected (Constitution A2.6). Silent acceptance is a release blocker.
5. Every component ships with its events, metrics, tests, and documentation (Constitution A11.5).
6. Secrets follow [SECURITY_POLICY.md](SECURITY_POLICY.md) without exception.
7. AI coding sessions additionally follow [AI_AGENT_RULES.md](AI_AGENT_RULES.md).

## 5. Architecture protection rules

- **Layer boundaries are CI-enforced** (Constitution A4.1). A build that violates import rules cannot merge, regardless of urgency.
- **No architectural change by drift.** Renaming a component, moving a responsibility, adding a cross-layer call, introducing shared state — each is an architectural change requiring an ADR, however small the diff.
- **The Pipeline Substrate stays confined** to its single module (Constitution D2). Any import of the substrate elsewhere is an automatic revert.
- **Provider neutrality is reviewed on every PR** (REVIEW_CHECKLIST §10): vendor names in `core/`, `runtime/` (outside `pipeline/`), or `api/` are defects.
- **Deprecation before deletion** for any public interface (Constitution A11.4): version, deprecate with a window, then remove.

## 6. Documentation requirements

Defined in [DOCUMENTATION_RULES.md](DOCUMENTATION_RULES.md); headline rule: **a change affecting architecture, runtime behaviour, or any public interface is incomplete without its documentation update in the same change-set.** Undocumented behaviour is treated as unspecified behaviour and may be removed.

## 7. ADR policy (headline; process in DOCUMENTATION_RULES.md)

- **Constitutional ADRs** amend Part A: full template (Problem, Context, Alternatives, Selected Solution, Trade-offs, Risks, Future Impact), owner approval required.
- **Profile ADRs** amend Part B: lightweight (what row changes, why, migration note).
- ADRs are numbered, immutable once accepted (superseded, never edited), and linked from `docs/INDEX.md`.
- No ADR, no architectural change. No exceptions for prototypes that will be "cleaned up later."

## 8. Implementation workflow

The 13-phase workflow in [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) is mandatory for every task, sized to the task (a one-line fix moves through the phases in minutes, but moves through them). Phase-skipping is itself a reviewable defect.

## 9. Code review expectations

- Every change is reviewed against [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md) — by a human, an AI reviewer, or both; solo-developer changes are self-reviewed **against the written checklist**, recorded in the PR/commit description.
- Reviews verify the change *and its documentation and tests* as one unit.
- A reviewer who finds an architecture violation must block, not comment-and-approve.
- Review findings that reveal a rule gap update the governance docs (via DECISION_LOG).

## 10. Release principles

- Releases are tagged, versioned ([GIT_WORKFLOW.md](GIT_WORKFLOW.md)), and accompanied by release notes and changelog entries.
- **Release gates (all mandatory):** green CI (lint, tests, import rules, contract suites) · latency budgets met ([PERFORMANCE_POLICY.md](PERFORMANCE_POLICY.md)) · no open Severity-High defects (including silent-config findings) · security checklist pass · documentation synchronized.
- A performance or security regression blocks the release — features never outrank gates.
- Rollback is a first-class path: every release must be rollback-safe (migrations reversible or expand-contract).

## 11. Quality expectations

- Defect severity ladder: **Critical** (data loss, security, cross-org leak) → fix immediately, everything else stops · **High** (architecture violation, silent config, latency budget breach, broken determinism) → fix before next merge to affected area · **Medium/Low** → scheduled, logged.
- Warnings are errors in CI. Flaky tests are quarantined within one day and fixed within one week or their feature is considered untested.
- Technical debt is only acceptable when logged in DECISION_LOG.md with an owner and a revisit date — undocumented debt is a defect.
