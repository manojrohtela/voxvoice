# Epic 6 Deferred Decisions Register

**Status:** Active, append-only · **Owner:** Technical Owner · **Origin:** M6.6.5 Documentation & ADR Closure.

This register collects every architectural item across Epic 6 (Tool, Skill,
Hook, Workflow, Agent, Loop) that was **identified and accepted as a known
gap**, but deliberately **not resolved** — either because no consumer/caller
exists yet, because resolving it would require an explicit unfreeze of a
certified/frozen capability, or because it is genuinely future-scoped work.
This is not a bug tracker: every item here was already named, in its own
capability's design doc or certification, as an intentional deferral —
this register exists only to consolidate them in one place, per the M6.6.5
milestone's own instruction ("collect deferred items... do not resolve them").

Each item is closed only by a future, explicitly-commissioned milestone that
resolves it — never silently, and never by this register itself.

---

## 1. AgentPort conformance

**Origin:** M6.5C Agent Runtime Production Certification (DL-0064), Medium severity.

Phase 0 (M6.0, DL-0042) named `core.ports.agent.AgentPort` as the port Agent
Runtime implements. The M6.5A design's own body never revisited that claim
the way the Workflow Runtime design explicitly deferred `WorkflowPort`
conformance as its own named ADR item. No bundle (M6.5B-1/2/3) ever checked
or implemented literal conformance. Unlike Tool Runtime's and Skill Runtime's
own conformance deferrals (both explicitly named as deliberate, deferred
decisions in their respective designs), Agent Runtime's case is a genuine gap
— found by re-reading the design doc's own opening paragraph against its ADR
section, not a decision anyone made on purpose.

**Not resolved here.** A future milestone should either implement
`AgentPort` conformance directly on `AgentExecutionRuntime`, or explicitly
re-scope the design doc to name it a deferred target the same way Tool/
Skill/Workflow's own equivalents are named.

## 2. Multi-workflow delegation limitation

**Origin:** M6.5C Agent Runtime Production Certification (DL-0064), **High** severity — the most consequential finding across all of Epic 6 certification to date.

`AgentGoalExecution.delegated_execution_id` is singular.
`AgentExecutionEngine.submit()`/`_revise_and_redelegate()` use
`next(iter(delegation.execution_ids.values()), None)`, silently tracking
only the *first* of a plan's possibly-several `delegate_workflow` steps —
additional delegated workflow executions are submitted but never polled to
completion by the goal FSM. Found by direct code inspection, not by a
failing test, since all 84 Bundle 2/3 tests only ever exercised single-
`delegate_workflow`-step plans.

**Not resolved here.** A future Agent Runtime unfreeze milestone must widen
`AgentGoalExecution` to track a collection of delegated execution ids (and
the goal FSM's `poll()`/`recover()` logic to aggregate their statuses) before
any real multi-step delegated plan can be trusted in production.

## 3. Loop composition-root strategy

**Origin:** M6.6A Loop Engine Design (§11 Risks) and M6.6C Production Certification, Medium severity.

Loop Engine deliberately ships no `LoopFactory`-as-composition-root and no
registry (ADR-0016) — every future adopter (Agent Runtime's own future
plan-revision step, Workflow Runtime's own future retry-with-improvement
node, a future MCP tool-call refinement) must independently wire
`ReasoningPort` + `LoopValidator` + `LoopDefinition` together. This is a
deliberate simplicity trade-off, not an oversight.

**Not resolved here.** Revisit only once three or more real callers exist
and are observed duplicating near-identical wiring — the threshold the
M6.6A design itself named.

## 4. `max_iterations` documentation clarification

**Origin:** M6.6C Loop Engine Production Certification, Medium severity.

`policy.max_iterations` is checked at the **start** of an iteration, before
that iteration's own critique/repair pass (inherited unchanged from Bundle
1's frozen `stopping.py`). A caller setting `max_iterations=N` therefore
gets at most `N-1` full critique/repair passes, not `N` — the literal,
correct reading of `iteration_number >= policy.max_iterations`, but an easy
boundary to misjudge when tuning a budget.

**Not resolved here** (no code change; this is a documentation gap, not a
defect). A future documentation-only pass should add a worked example to
the M6.6A design doc's own §6 stopping-policy table.

## 5. Registry `versions_for` O(N) full-scan pattern

**Origin:** repeated across Skill Runtime (M6.2B-3), Workflow Runtime (M6.4C), and Agent Runtime (M6.5C) certifications — each Medium severity, same root cause.

Each of these three registries resolves `versions_for(namespace, id)` via a
full linear scan rather than an indexed lookup. Accepted as a trade-off at
current expected registry sizes (dozens to low hundreds of registered
entities per namespace, not millions); flagged each time as a known,
repeated pattern rather than three independent bugs.

**Not resolved here.** Revisit if/when a registry's expected entity count
grows large enough for O(N) scans to matter in practice — no such case has
been observed yet.

## 6. ADR-ledger gap (now closed by this very milestone for the items it covers)

**Origin:** repeated across Skill Runtime (M6.2B-3), Hook Framework (M6.3C), Workflow Runtime (M6.4C), and Agent Runtime (M6.5C) certifications — each Medium severity, now a 5th repetition including Loop Engine (M6.6C).

Every one of these five certifications found that the design doc's own
recommended ADR items had never been materialized as standalone
`docs/adr/` files, despite being cited by number in later documents (e.g.
`workflow-runtime-design.md` §12 item 4 citing "Phase 0's already-proposed
ADR-0009"). **This milestone (M6.6.5) resolves this gap for every decision
that has actually been accepted and certified** — see ADR-0008 through
ADR-0017. It remains open only for:
- Any future Epic 6 capability's own decisions, once designed/certified.
- ADR-0011 (MCP/A2A bridge-pattern) — reserved but correctly *not*
  materialized here, since MCP/A2A do not exist yet and this milestone's own
  instruction forbids ADRs for speculative ideas.

## 7. `AgentExecutionContext` / `LoopExecutionContext` declared-but-unconsumed shapes

**Origin:** M6.5C Agent Runtime Production Certification (DL-0064) and M6.6C Loop Engine Production Certification, each Medium severity.

`AgentIdentity`/`AgentConversation`/`AgentExecutionContext` are declared and
structurally tested but never constructed or consumed by any of Agent
Runtime's three bundles. `LoopExecutionContext` is likewise declared,
constructed by `LoopFactory.create_context()`, and tested, but never
actually threaded into or consumed by `run_loop()` itself (which takes raw
`session_id`/`org_id` parameters directly instead). Both are the same
"declared shape, never wired" pattern.

**Not resolved here.** The natural trigger for `AgentExecutionContext` is a
future multi-goal-conversation or cross-runtime-context-propagation feature;
for `LoopExecutionContext`, whichever future caller first needs to thread
loop-carried `variables`/`iteration` state between calls (most likely Agent
Runtime's own future plan-revision step).

## 8. Hook Framework integration into certified/frozen runtimes

**Origin:** M6.3A Hook Framework Design (§12 ADR item 5), still open.

Tool Runtime and Skill Runtime are both certified and frozen; wiring Hook
Framework call-sites into either requires an explicit unfreeze decision for
that runtime, not a unilateral Hook Framework change. Workflow Runtime's own
design recommends (but does not perform) an additive 3-member `HookPoint`
extension (`before_workflow`/`after_workflow`/`workflow_failure`) for
whole-execution hooks.

**Not resolved here.** Each integration remains scoped to its own future,
explicitly-commissioned unfreeze milestone.

## 9. Tool Runtime approval-gate (`PENDING` verdict) resolution mechanism

**Origin:** M6.1A Tool Runtime Design (§18 Risks), still open.

The `PENDING` policy verdict (ADR-0012) has no consumer yet — nothing in
Epic 5 or Epic 6 so far defines who resolves a pending approval (a
human-in-the-loop UI/API). Explicitly named as deferred to Developer
Platform (M6.10) or a dedicated later milestone.

**Not resolved here.**

## 10. `WorkflowPort` conformance

**Origin:** M6.4A Workflow Runtime Design (§11), certified but unresolved.

Literal `core.ports.workflow.WorkflowPort` (`start`/`signal`/`status`)
conformance is a design target for `WorkflowExecutionRuntime`, not a
verified guarantee — recorded explicitly, mirroring Tool Runtime's own
honestly-deferred `ToolPort` conformance debt (item below). Agent Runtime's
own `AgentWorkflowPort` seam (ADR-0015) was deliberately built as a
consequence of this exact gap.

**Not resolved here.** If a future milestone verifies or implements this
conformance, `AgentWorkflowPort`'s adapter implementation should be
revisited to delegate to the real port rather than duplicating logic (noted
as a soft dependency between two open items in ADR-0015's own Consequences).

## 11. `ToolPort` conformance

**Origin:** M6.1B-1 Tool Runtime Core, certified but unresolved (mirrors item 10 above; the first instance of this pattern in Epic 6, later repeated for Skill's `SkillPort` — never anticipated by any M5.0 port — and Workflow's `WorkflowPort`).

**Not resolved here.**

---

## Consistency note

Items 10 and 11 (port-conformance deferrals) were deliberately **not**
turned into their own ADR in this milestone, unlike items that already
crossed from "identified gap" to "ratified architectural decision" (e.g.
ADR-0015, which ratifies Agent Runtime's own *response* to Workflow's
unverified conformance). The conformance gaps themselves remain open
questions, not decisions — recording them as ADRs would misrepresent an
unresolved gap as a considered, accepted trade-off. They stay here instead.
