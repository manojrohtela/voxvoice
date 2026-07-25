# ADR-0014 — Structure, intelligence, and correctness are three distinct ownership categories

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture |
| **Deciders** | Technical Owner |
| **Related** | [agent-runtime-design](../architecture/epic6/agent-runtime-design.md) §11 item 2, [loop-engine-design](../architecture/epic6/loop-engine-design.md) §3, §9 item 2, DL-0042, DL-0060, DL-0064, DL-0065, DL-0067 |

## Status

Accepted. First applied to Agent Runtime's planning model (M6.5A, ratifying
Phase 0's own DL-0042 point (b) at full-design level; certified M6.5C).
Extended a second time by Loop Engine (M6.6A), which introduces a fourth,
previously-unneeded category (correctness) and delegates it separately from
intelligence; certified M6.6C at 93/100 with this split confirmed intact by
direct code inspection.

## Context

Both Agent Runtime and Loop Engine need to generate and refine content
(a plan, a proposal) using a language model, and both need to decide whether
that content is good enough. Two failure modes were possible: (a) building a
second, bespoke inference/planning engine inside `agent/` or `loop/` instead
of using the already-frozen `core.ports.reasoning.ReasoningPort`, and (b)
conflating "the model judged this favorably" (a soft, LLM-produced assessment)
with "this is actually correct" (a hard, domain-specific check) — letting the
model's own reasoning silently stand in for real correctness validation.

## Decision

Three (for Loop Engine, four) genuinely distinct ownership categories:
1. **Structure** — the calling package (`agent/`, `loop/`) owns request
   construction and deterministic response parsing only. It never generates
   content itself.
2. **Intelligence** — content generation is entirely `ReasoningPort.run`'s
   responsibility (Reasoning Runtime, behind a model-vendor-agnostic port).
   Agent's goal decomposition/plan revision and Loop's propose/critique/repair
   are all ordinary `ReasoningPort.run` calls, deterministically parsed the
   same way (`decompose_goal`'s `Decision(kind=FINAL)` parse, applied a
   second time by Loop Engine's own pipeline).
3. **Correctness** (Loop Engine only) — whether a proposal is actually right
   is never Loop Engine's own judgment call; it is entirely delegated to an
   injected `LoopValidator`, a fourth category distinct from both structure
   and intelligence. Critique (soft, LLM-judged, feeds repair) and validation
   (hard, domain-specific, feeds the stop decision) must never be conflated —
   they are architecturally distinct even though both "assess" a proposal.

## Consequences

- Neither `agent/` nor `loop/` will ever contain a second, competing
  inference/planning engine — any future temptation to "just special-case
  this one prompt pattern locally" is foreclosed by this record.
- A future Loop Engine caller cannot satisfy the "validation" requirement by
  routing it through the same `ReasoningPort` call used for critique — the
  `LoopValidator` seam is a distinct, separately-injected dependency, and
  conflating the two would silently let the model's own opinion stand in for
  the caller's actual correctness check, exactly what this decision forecloses.
- This pattern is available as a template for any future Epic 6 capability
  that needs both content generation and a distinct correctness gate (e.g. a
  future MCP/A2A capability evaluating tool-call results) — cited, not
  re-derived from scratch.

## Alternatives Considered

- **Build planning/refinement intelligence directly inside `agent/`/`loop/`
  (a bespoke prompt-and-parse engine per capability).** Rejected — duplicates
  Reasoning Runtime's own already-certified responsibility, fragments model-
  vendor abstraction across packages, and reopens exactly the "second
  competing intelligence engine" risk this decision exists to foreclose.
- **Let Loop Engine's own critique step double as its validation step (no
  separate `LoopValidator`).** Rejected — an LLM's own soft, self-reported
  assessment of its own proposal is not equivalent to an independent,
  domain-specific correctness check; conflating them would mean "the model
  says it's fine" silently satisfies what should be an external gate.
- **Give Loop Engine's `LoopValidator` access to `ReasoningPort` itself, so it
  can optionally use the model for validation too.** Rejected as unnecessary
  scope — `LoopValidator` is the caller's own domain logic; if a caller wants
  to build a model-backed validator, that is the caller's implementation
  choice, not something Loop Engine needs to wire in itself.
