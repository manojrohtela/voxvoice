# AI_AGENT_RULES.md — Rules for Every AI Coding Session

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · **Applies to:** every AI agent (Claude Code or other) working in this repository, every session, no exceptions.

These rules bind AI sessions *in addition to* all other governance. Where an AI agent and a governance document disagree, the document wins. Where documents are silent, the agent follows the Constitution's philosophy (A2) and asks.

## Prime directives

1. **Never violate the Engineering Constitution.** Read the relevant Part A sections before designing; cite the section in your plan. If a task cannot be done constitutionally, stop and say so.
2. **Never guess.** If an API shape, provider behaviour, schema, or requirement is unknown — verify it (read code, read docs, run it) or ask. A guess presented as fact is the worst defect class an AI can produce. Mark every unverified assumption explicitly as an assumption.
3. **Never silently modify architecture.** Moving responsibilities, adding dependencies between layers, changing an event's producer, adding shared state — these require an ADR *before* code, even if the diff is small.
4. **Never ignore ADRs.** Accepted ADRs are law until superseded. If an ADR seems wrong, propose a superseding ADR; do not code around it.
5. **Stop and explain conflicts instead of making unsafe decisions.** When requirements conflict with governance, or two governance rules collide, halt the implementation, present the conflict and options with trade-offs, and wait. An unsafe "helpful" workaround is a violation, not a favour.

## Mandatory behaviour

6. **Follow the DEVELOPMENT_WORKFLOW phases** and say which phase you are in.
7. **Always explain architectural trade-offs** when more than one compliant design exists — recommendation first, alternatives briefly.
8. **Always update documentation in the same change-set** as the behaviour it describes (DOCUMENTATION_RULES). This includes the component spec (Constitution A11.3) for any touched runtime component.
9. **Always validate before committing:** run the tests, the linter, and the import-layer check; report actual output, never "should pass."
10. **Report outcomes faithfully.** If tests fail, say so with the failure. If a step was skipped, say so and why. Never describe intended behaviour as verified behaviour.
11. **Persist knowledge.** Verified external facts (provider API shapes, environment quirks) go into `docs/research/` or `docs/providers/`, not just the chat.
12. **Respect the glossary** (Constitution A0). One concept, one name, in code identifiers and docs alike.

## Prohibitions

13. **Never leave TODOs in merged code.** A TODO is either done, converted to a logged DECISION_LOG/backlog item with an owner and reference in the code comment (`// DEFERRED(DL-042): …`), or the merge is blocked.
14. **Never duplicate logic.** Search for the existing implementation first; extend or reuse it. Copy-paste-adjust across modules is a defect.
15. **Never create temporary fixes** ("hack for now", magic sleeps, retry-until-it-works, commented-out code). If a proper fix is too large, log the decision and implement the smallest *correct* subset.
16. **Never ignore failed tests** — not by skipping, not by loosening assertions, not by deleting. A failing test is either a real defect (fix the code) or a wrong test (fix the test, explain why in the commit).
17. **Never break compilation/imports on the main branch.** Every commit builds and passes fast checks.
18. **Never introduce hidden dependencies:** no imports that violate layer rules, no reaching into another module's privates, no undeclared environment variables, no implicit ordering requirements between components. New configuration appears in the settings schema + `.env.example` + docs, or not at all.
19. **Never hardcode what the Constitution says is configuration** (thresholds, timeouts, budgets, provider selections) and never add configuration the runtime doesn't honour (A2.6 — both directions).
20. **Never touch secrets:** never print, log, commit, or echo credential values; never write them into examples; flag any secret found in the tree immediately.
21. **Never expand scope silently.** Implement what the task says; list discovered adjacent problems in the report instead of fixing them unasked (unless they block the task, in which case say so first).
22. **Never delete or rewrite documents marked immutable** (audits, accepted ADRs) — supersede them.

## Session discipline

23. Begin substantial work by stating: the requirement, the affected components (with Constitution sections), and the plan.
24. End every task by reporting against DEFINITION_OF_DONE — item by item, honestly, including which items were not applicable and which are pending human action.
25. If context is lost mid-task (session restart, compaction), re-read the governing documents for the touched area before continuing — do not proceed from memory of them.
26. When two AI sessions' changes collide, the later session reconciles both against governance rather than overwriting.

## Additional long-term product rules

27. Prefer boring, explicit code over clever code; the next reader is the priority (CODING_STANDARDS).
28. New public interfaces ship versioned from day one (Constitution A11.4).
29. Every new runtime code path emits its events and latency taps in the same PR — observability is not a follow-up.
30. When benchmarking or measuring, record the environment and method with the numbers; unreproducible numbers are anecdotes.
