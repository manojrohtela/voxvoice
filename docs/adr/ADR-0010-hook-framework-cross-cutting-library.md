# ADR-0010 — The Hook Framework is a cross-cutting library, not a `core.ports` runtime

| | |
|---|---|
| **Status** | Accepted 2026-07-25 |
| **Amends** | Epic 6 Agent Platform Architecture (ratifies a decision proposed, not yet accepted, at Phase 0) |
| **Deciders** | Technical Owner |
| **Related** | [epic6-agent-platform-architecture](../architecture/epic6/epic6-agent-platform-architecture.md) §18, [hook-framework-design](../architecture/epic6/hook-framework-design.md) §12 item 1, DL-0042, DL-0053, DL-0055 |

## Status

Proposed at Phase 0 (M6.0, DL-0042, "ADR-0010 (proposed)"). Formally ratified
here now that the complete Hook Framework design (M6.3A), its single-bundle
implementation (M6.3B, DL-0054), and its production certification (M6.3C,
DL-0055, PASS) all confirm the decision held through implementation.

## Context

Every other closed Epic 6 capability (Tool, Skill, Workflow, Agent) implements
a frozen `core.ports.*` interface — that port is *how* other runtimes reach it.
The Hook Framework is different in kind: it is not a capability another
runtime *calls into* for a result; it is cross-cutting lifecycle interception
(`before_*`/`after_*`/`*_failure` events) that other runtimes *consume* by
registering hooks against a shared `HookPoint` vocabulary. Giving it a
`core.ports.hooks.HookPort` would misrepresent this relationship and invite a
future contributor to wire it the way Tool/Skill/Workflow are wired (a
single call-in interface), which does not fit its actual usage pattern
(many runtimes registering against many lifecycle points).

## Decision

The Hook Framework has no `core.ports` entry. It is consumed *by* runtimes
(each runtime owns its own decision to call into `HookRuntime` at its own
lifecycle points), never *as* one itself. It does, however, apply the
platform's standard manifest/descriptor/registry/lifecycle pattern
(`HookManifest`/`HookDescriptor`/`HookRegistry`) since hooks are still
registrable, versioned, and independently lifecycle-managed by third parties
— the *port* omission is specific to the call-direction relationship, not a
rejection of the registrable-entity pattern generally.

## Consequences

- No future contributor should "complete" the port pattern by adding a
  `HookPort` — doing so would suggest hooks are called *into* the way
  Tool/Skill/Workflow/Agent are, which is backwards from how lifecycle
  interception actually works.
- Each already-certified/frozen runtime's own decision to call into
  `HookRuntime` at specific lifecycle points is an *additive*, per-runtime
  integration choice (e.g. Workflow's own recommended `before_workflow`/
  `after_workflow`/`workflow_failure` `HookPoint` additions, workflow-runtime-
  design.md §12 item 5) — never a Hook Framework redesign forcing every
  runtime to integrate at once.
- No direct dependency exists between `hooks/` and any Tool/Skill/Workflow/
  Agent package in either direction; any future wiring of a certified/frozen
  runtime to call into `HookRuntime` requires that runtime's own explicit
  unfreeze decision, never a unilateral Hook Framework milestone reaching in.

## Alternatives Considered

- **Give the Hook Framework its own `core.ports.hooks.HookPort`, mirroring
  every other capability.** Rejected — misrepresents the call direction
  (runtimes call *into* hooks at their own lifecycle points; hooks do not
  provide a service other runtimes invoke for a result the way Tool/Workflow
  do), and would invite exactly the wrong integration pattern.
- **Treat the Hook Framework as pure internal utility code with no
  registrable identity at all (Loop Engine's own later pattern, ADR-0016).**
  Rejected — hooks genuinely are registrable, versioned, and independently
  lifecycle-managed (a third party can ship a hook manifest), unlike Loop
  Engine's single-invocation, caller-supplied-inputs shape; omitting the
  manifest/descriptor/registry pattern here would lose real, needed
  discoverability.
- **Wire Hook Framework directly into Tool/Skill Runtime's own execution path
  now, since it's a small change.** Rejected (design's own ADR item 5) —
  Tool Runtime is certified and frozen; any change to it, however small,
  requires an explicit unfreeze decision, not a Hook Framework milestone
  unilaterally reaching in.
