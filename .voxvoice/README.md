# `.voxvoice/` — Engineering Governance System

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority level:** 3 (see hierarchy below)

The [Engineering Constitution](../docs/architecture/engineering-constitution-v2.md) (ratified v2.0) defines **what** the architecture is. This directory defines **how** engineers — human and AI — work within it. Every implementation task is bound by these documents. If an implementation conflicts with any governance document, implementation **stops** until the conflict is resolved at the correct authority level.

## Authority Hierarchy (binding)

1. **Engineering Constitution** (`docs/architecture/engineering-constitution-v2.md`) — Part A architecture; Part B Implementation Profile
2. **ADRs** (`docs/adr/`) — constitutional ADRs amend Part A; profile ADRs amend Part B
3. **Governance documents** (this directory)
4. **Technical Design Documents** (`docs/architecture/`, `docs/runtime/`)
5. **Sprint plans** (`docs/roadmap/`)
6. **Implementation tasks**
7. **Source code**

A lower level may never contradict a higher level. Conflicts are resolved *upward*: stop, document the conflict in [DECISION_LOG.md](DECISION_LOG.md), and either change the lower artifact or raise an ADR against the higher one. Code is evidence of what *is*, never an argument for what *should be*.

## Document Index

| Document | Governs | One-line scope |
|---|---|---|
| [GOVERNANCE.md](GOVERNANCE.md) | Everything | Supreme engineering policy; philosophy, priorities, protection rules, release principles |
| [AI_AGENT_RULES.md](AI_AGENT_RULES.md) | Every AI coding session | Mandatory behaviour, prohibitions, stop conditions |
| [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) | Every task | The 13-phase mandatory implementation workflow |
| [CODING_STANDARDS.md](CODING_STANDARDS.md) | All source code | Naming, structure, errors, logging, async, config, comments |
| [ARCHITECTURE_RULES.md](ARCHITECTURE_RULES.md) | All design & code | Constitution Part A converted to enforceable rules (AR-1…AR-30) |
| [DOCUMENTATION_RULES.md](DOCUMENTATION_RULES.md) | All documentation | Structure, sync policy, ADR workflow, ownership, changelogs |
| [TESTING_POLICY.md](TESTING_POLICY.md) | All testing | Test taxonomy, when each type is required, quality bars |
| [PERFORMANCE_POLICY.md](PERFORMANCE_POLICY.md) | All runtime code | Latency budgets (defers to Constitution A8), limits, profiling, regression gates |
| [SECURITY_POLICY.md](SECURITY_POLICY.md) | Everything | Secrets, authn/z, encryption, vulnerabilities, audit |
| [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md) | Every review | The 14-dimension mandatory review checklist |
| [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md) | Every feature | The completion checklist; nothing merges without it |
| [GIT_WORKFLOW.md](GIT_WORKFLOW.md) | Version control | Branching, commits, PRs, releases, hotfixes, rollback |
| [DECISION_LOG.md](DECISION_LOG.md) | Decisions below ADR level | Structured running log of engineering decisions |

## Cross-Reference Map (governance ⇄ Constitution)

| Governance document | Enforces / defers to Constitution sections |
|---|---|
| GOVERNANCE.md | Whole document; authority model from v2 header; A13 documentation constitution |
| AI_AGENT_RULES.md | A2 (philosophy), A11.1 (constitutional rules), A2.6/A6.1 (config must act) |
| DEVELOPMENT_WORKFLOW.md | A11.5 (quality gates), A11.3 (component specs), A13 (doc sync) |
| CODING_STANDARDS.md | A11.1.7 (banned anti-patterns), A14 (folder structure), Part B (language profile) |
| ARCHITECTURE_RULES.md | A2 (one writer per fact), A4.1 (layer rules), A5 (event ownership), A6 (provider rules), A9 (no cross-request in-process state), A12 (additive-only) |
| DOCUMENTATION_RULES.md | A13 (documentation constitution), A11.3 (component specs), A11.4 (versioning) |
| TESTING_POLICY.md | A11.5 (contract suites, state-machine tests, fakes), A6.3 (plugin = contract test) |
| PERFORMANCE_POLICY.md | I1 (latency invariant), A8 (stage budgets), A11.5 (regression gates) |
| SECURITY_POLICY.md | A11.2 (security & compliance), A11.1.6 (secrets) |
| REVIEW_CHECKLIST.md | A11 (all standards), I1–I3 (invariants) |
| DEFINITION_OF_DONE.md | A11.5 (quality gates), A13 (doc sync), A11.3 (component spec) |
| GIT_WORKFLOW.md | A11.5 (trunk-based small commits) |
| DECISION_LOG.md | A13 (ADR policy — the log records what ADRs don't) |

**Overlap authority rules:** where a topic appears in two places, the numbers/budgets live in the Constitution (A8, I1) and governance documents *reference* them; the ADR *process* lives in DOCUMENTATION_RULES.md and GOVERNANCE.md defers to it; review *dimensions* live in REVIEW_CHECKLIST.md and DEFINITION_OF_DONE references it; coding *rules* live in CODING_STANDARDS.md and architecture *rules* in ARCHITECTURE_RULES.md (a rule about imports/boundaries is architectural; a rule about style/idiom is coding).

## Terminology

All governance documents use the Constitution's Glossary (A0) verbatim: *Consumer, Caller, Agent, Session, Turn, Control Plane, Runtime Plane, Session Worker, Provider Plugin, Capability, Pipeline Substrate, Event, System of Record, Event Bus, Turn Intelligence, Implementation Profile.* No synonyms.

## Governance change control

Governance documents are living (unlike audits). Changes require: an entry in DECISION_LOG.md, a version bump in the document header, and — if the change alters a rule that code already relies on — a migration note. Governance may never be weakened to accommodate a specific in-flight task; weaken first (with rationale), implement second.
