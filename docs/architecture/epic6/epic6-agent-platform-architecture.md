# Epic 6, Phase 0 — Agent Platform Architecture & Capability Design

| | |
|---|---|
| **Scope** | The next-generation Agent Platform sitting atop the frozen Epic 5 AI Runtime Platform |
| **Status** | 🟡 Design only — no implementation in this phase |
| **Basis** | Epic 5 (frozen, `epic5-runtime-platform-v1.0`): Memory/Knowledge/Context/Prompt/Reasoning/Provider Runtimes + the Runtime Pipeline; the frozen `core.ports.{tool,workflow,agent}` interfaces (M5.0, unexercised until now); the existing `api`/`gateway`/`replay`/`telemetry` platform infrastructure |
| **Governs** | Nothing yet — this document is the approved blueprint Epic 6's implementation milestones (Phase 1+) will build to, mirroring Epic 5's own design→core→intelligence sequencing |

> **Design-only phase.** No code in this document. It redesigns **nothing** in Epic 5 —
> every capability below is placed either (a) behind an already-frozen, already-unexercised
> `core.ports` interface (`ToolPort`, `WorkflowPort`, `AgentPort`), or (b) as a wholly new,
> additive top-level package that consumes Epic 5 runtimes only through their existing public
> surfaces (`ReasoningRuntime.infer`, the `pipeline.RuntimePipeline`, `ProviderModelBridge`
> pattern) — never their internals.

---

## 1. Executive Summary

Epic 5 delivered a deterministic, provider-neutral **AI Runtime Platform**: six runtimes
plus a composition-root pipeline that turns a request into one unified response. Epic 6
builds the **Agent Platform** on top of it — the layer that turns that single deterministic
call into autonomous, tool-using, multi-step, multi-agent behavior, exposed through a
developer-facing surface (REST/WebSocket/SDK/CLI/Dashboard). Nothing in Epic 5 is touched:
three of Epic 6's seven new runtimes (Tool, Workflow, Agent) implement ports the M5.0
Foundation already froze and left deliberately unexercised for exactly this purpose; the
rest (Skill, Hook, Loop, MCP, A2A) are wholly new, additive packages that consume Epic 5 and
each other only through ports, registries, and composition-root bridges — the identical
pattern that kept all eight Epic 5 packages acyclic. Every capability retains a single,
unambiguous owner (§4); every provider/vendor concept referenced in research (§3) is
extracted as an architectural pattern, never copied as an implementation.

## 2. Epic 6 Vision

**One sentence:** Epic 5 answered *"how does one deterministic call flow through the AI
stack;"* Epic 6 answers *"how does an agent decide to make that call, more than once, with
tools, according to a plan, possibly with help from other agents — and how does a developer
build, observe, and ship one."*

Design commitments, carried forward unchanged from Epic 5's own discipline:
- **Provider/LLM/framework/cloud/vendor-neutral** — no runtime below the Provider Runtime
  changes; nothing in Epic 6 imports a vendor SDK either (adapters/tools are the only
  vendor-aware code, exactly as `AdapterPort` already established).
- **Deterministic & replay-safe** — every new runtime gets its own `core.fsm` lifecycle,
  content-hash ids, and a place in the existing `replay/` engine's event stream. Where an
  LLM's own non-determinism is unavoidable (agent planning, loop critique), the *orchestration*
  around it stays deterministic and replayable — the model is the injected, non-deterministic
  seam, exactly as Reasoning already established with `ModelProvider`.
- **Composable & extensible** — every new capability is a registry + a port + a factory,
  never a monolith.
- **Enterprise-ready** — tenancy, policy, and audit hooks are threaded through every new
  runtime from its first (not second) milestone, correcting an intentional Epic 5 sequencing
  choice (tenancy always landed in each runtime's *second* milestone) now that the pattern is
  proven and the cost of deferring it is better understood.
- **Single owner per capability** — see the ownership matrix, §4.

## 3. Capability Analysis (research-grounded; concepts extracted, nothing copied)

The following public architectural concepts were studied for their *patterns*, not their
implementations. No code, prompt, or proprietary detail from any of these systems appears
below — only the reusable shape.

| Source concept | Pattern extracted | Where it lands in Epic 6 |
|---|---|---|
| **Claude Skills** (packaged, discoverable capability bundles with instructions + optional resources, loaded on demand) | A **versioned, declarative asset** (not code) that a runtime resolves and injects into context/tools at plan time, distinct from a Tool (which *executes*) | Skill Runtime (§8) — a manifest+registry, not a code package |
| **Claude Hooks** (lifecycle interception points around tool/agent actions, configurable per-event, can block/modify) | **Deterministic interception points** at named lifecycle boundaries, each with an explicit allow/deny/modify verdict and a failure policy | Hook Framework (§9) |
| **Claude Code architecture** (a host loop that plans, calls tools, observes results, and iterates under a visible policy, with permission gating per tool) | The **agent loop as an explicit state machine** (plan → act → observe → repeat) rather than an implicit prompt loop; permission/policy gating on every tool call | Agent Runtime (§11) + Hook Framework's permission-gate pattern |
| **Loop Engineering / Reflective execution** (critic/verifier passes, repair loops, bounded iteration) | A **generic, reusable loop shape** — propose → critique → repair → stop-check — parameterized over what "critique" means | Loop Engine (§6, §12) as a shared primitive Agent/Workflow both use |
| **Anthropic MCP** (a standard client/server protocol for exposing tools/resources/prompts to a model host, with capability negotiation and sessions) | **Protocol-level tool/resource exposure with capability negotiation**, decoupled from any specific host | MCP Runtime (§7, §12) — a protocol adapter *in front of* the existing `ToolPort`, never a replacement for it |
| **Google A2A** (agent cards advertising capabilities, task-based cross-agent messaging, discovery) | **Capability advertisement + task-oriented messaging** between autonomous peers, with its own auth/discovery layer | A2A Runtime (§8, §13) |
| **OpenAI Agents SDK** (agents-as-config with tools/handoffs/guardrails, a run loop, tracing) | **Agent-as-declarative-config** (not agent-as-inheritance), explicit **handoff** as a first-class transition, guardrails as a named policy layer | Agent Runtime's `AgentRef`-as-config shape (already frozen in `core.ports.agent.AgentRef`!) + handoff as a first-class `AgentPort.handoff` call |
| **Tool calling patterns** (JSON-schema arguments, typed results, sandboxed execution, retry/timeout policy) | Already exactly what `core.ports.tool.ToolPort`/`ToolSpec`/`ToolCall`/`ToolResult` froze at M5.0 | Tool Runtime (§7) implements this port directly — no new shape needed |
| **Workflow engines** (DAG/graph execution, branching, retries, durable state, replay) | **Declarative graphs, not code-as-orchestration** — exactly what `core.ports.workflow.WorkflowPort`'s docstring already commits to | Workflow Runtime (§10) |
| **Multi-agent systems** (coordinator/delegate patterns, shared blackboard memory, conflict resolution) | A **coordinator that never executes agent logic itself**, delegating through the same `AgentPort.handoff` seam single agents use | Multi-Agent Platform (§15) — no new "coordination protocol," just orchestrated handoffs + shared Memory-Runtime scope |

**Non-goals extracted from research (what NOT to copy):** vendor-specific prompt formats,
vendor-specific tool-call wire formats (MCP's JSON-RPC shape is a protocol detail
implemented *inside* the MCP Runtime adapter, never leaking into `ToolPort`), and any
framework's specific "agent base class" inheritance model (Epic 6 follows Epic 5's
composition-over-inheritance discipline throughout).

## 4. Runtime Ownership Matrix

| Runtime / Package | Owns | Never owns | Implements |
|---|---|---|---|
| **Tool Runtime** (`tool/`) | Tool registration, schema validation, sandboxed invocation, typed results, per-tool retry/timeout | Planning, reasoning, workflow state, agent identity | `core.ports.tool.ToolPort` (frozen, M5.0) |
| **Skill Runtime** (`skill/`) | Skill manifest resolution, versioning, discovery, injection into Prompt/Context at plan time | Execution (skills never run — they describe, Tools execute), tool invocation itself | New port: `SkillPort` (Epic 6, additive) |
| **Hook Framework** (`hooks/`) | Lifecycle interception, verdicts (allow/deny/modify), ordering, isolation, failure policy | Any business action itself — a hook only decides, never acts | New: `HookRegistry` (no `core.ports` entry needed — an internal cross-cutting seam, not a runtime boundary) |
| **Workflow Runtime** (`workflow/`) | Execution graphs, branching, retry/backoff at the graph level, durable state, replay | Tool execution, agent planning, model calls | `core.ports.workflow.WorkflowPort` (frozen, M5.0) |
| **Agent Runtime** (`agent/`) | Agent identity/config, activation, handoff, the plan→act→observe loop, tool/skill/memory scoping per agent | Tool execution, workflow graph execution, the model itself, provider selection | `core.ports.agent.AgentPort` (frozen, M5.0) |
| **Loop Engine** (`loop/`) | The generic propose→critique→repair→stop-check primitive; stopping criteria; loop statistics | Any specific critique logic (that's the injected critic's job) | New: `LoopEngine` (a library, not a `BusConsumer` runtime — see §6) |
| **MCP Runtime** (`mcp/`) | MCP protocol sessions, capability negotiation, translating MCP tool/resource exposure into `ToolPort` registrations | Tool execution itself (delegates to Tool Runtime), agent logic | New port: `MCPSessionPort` (Epic 6, additive) |
| **A2A Runtime** (`a2a/`) | Agent card publication, cross-agent discovery, task-based messaging, routing, authentication of peer agents | Agent execution itself (delegates to Agent Runtime via `AgentPort`) | New port: `A2APort` (Epic 6, additive) |
| **Multi-Agent Platform** (`multiagent/`) | Coordinator logic: delegation, scheduling, conflict resolution across agents | Individual agent execution (delegates to Agent Runtime), tool/workflow execution | Composition over `AgentPort` + `WorkflowPort` — no new frozen port needed |
| **Developer Platform** (`api/`, `gateway/`, new `sdk/`, `cli/`, `dashboard/`) | Exposing every above capability over REST/WebSocket/SDK/CLI/Dashboard; auth, rate limiting, observability surfacing | Any runtime's business logic — the Developer Platform is presentation only | Extends the **existing** `api`/`gateway`/`telemetry`/`replay` packages; does not reinvent them |

**Single-owner rule preserved exactly as in Epic 5:** no capability above appears in two
rows' "owns" column; every "never owns" line names the row that does own it.

## 5. Package Structure

```
platform/
  # ── frozen (Epic 5) — untouched ──────────────────────────────────────────────
  core/ tenant/ memory/ knowledge/ context/ prompt/ reasoning/ provider/ bridge/ pipeline/

  # ── Epic 6, Phase 1+ (new, additive) ────────────────────────────────────────
  tool/                    Tool Runtime — implements core.ports.tool.ToolPort
    domain.py                ToolDefinition, ToolInvocation, ToolExecutionResult (extends
                             the frozen ToolSpec/ToolCall/ToolResult with lineage/timing)
    registry.py                ToolRegistry (register/discover/lifecycle — mirrors
                               ProviderRegistry's "no routing/no scoring" discipline)
    sandbox.py                  SandboxPort (interface only; a real sandbox is a Non-goal
                               of Phase 1 — an in-process deterministic executor ships first,
                               mirroring EchoProvider/EchoProviderAdapter)
    policies.py                  Allow-list/deny-list/timeout/retry-budget (reuses
                                core.policies.RetryPolicy/TimeoutPolicy)
    runtime.py                    ToolRuntime(BusConsumer, ToolPort)
    factory.py, config.py, serializer.py, repositories/, stores/, observability.py, events.py

  skill/                   Skill Runtime — manifest + registry, no execution
    domain.py                SkillManifest (id, version, instructions, resources, required
                             capabilities — see §8 for the format decision)
    registry.py                SkillRegistry (versioned; resolve-by-id-and-version)
    resolver.py                  Injects a resolved skill's instructions into a Prompt
                                section or an Agent's plan context (via PromptPort/
                                AgentPort — never a direct Prompt/Agent internal call)
    runtime.py, factory.py, config.py, serializer.py, repositories/, events.py

  hooks/                   Hook Framework — cross-cutting interception, not a runtime
    domain.py                HookEvent (lifecycle point), HookVerdict (allow/deny/modify)
    registry.py                HookRegistry (ordered, per-event-point registration)
    engine.py                  HookEngine (runs registered hooks for a point; deterministic
                              order; isolation; failure policy — see §9)
    events.py, observability.py

  workflow/                Workflow Runtime — implements core.ports.workflow.WorkflowPort
    domain.py                WorkflowGraph, WorkflowNode, WorkflowEdge, WorkflowInstance
    lifecycle.py                core.fsm instance lifecycle (CREATED→RUNNING→PAUSED→
                              COMPLETED/FAILED/CANCELLED)
    engine.py                    Graph executor (branching/conditions/parallel fan-out —
                                orchestration only, delegates every node's actual work to
                                Tool/Agent/Reasoning via their own ports)
    repositories/                  DURABLE (not temporary!) — workflow state is the one
                                 Epic 6 runtime that must survive a process restart; uses
                                 core.providers.stores the same way, but with durable=True
    runtime.py, factory.py, config.py, serializer.py, stores/, observability.py, events.py

  agent/                   Agent Runtime — implements core.ports.agent.AgentPort
    domain.py                AgentDefinition (extends the frozen AgentRef: persona, tool
                             allow-list, skill allow-list, memory scope, model policy)
    lifecycle.py                core.fsm agent-turn lifecycle (IDLE→PLANNING→ACTING→
                              OBSERVING→DONE/HANDED_OFF/FAILED)
    planner.py                  Deterministic planning orchestration — the model is the
                               injected seam (reuses ReasoningRuntime.infer, never a new
                               model integration)
    loop.py                      Wires the Agent's plan→act→observe cycle onto the shared
                                loop/ Loop Engine (§6)
    runtime.py, factory.py, config.py, serializer.py, repositories/, stores/,
    observability.py, events.py, extensions.py (AgentBridge/WorkflowBridge already
    anticipated in reasoning/extensions.py — Agent Runtime is the concrete implementation)

  loop/                    Loop Engine — a shared library, not a BusConsumer runtime
    domain.py                LoopState, LoopStep, LoopOutcome
    engine.py                  LoopEngine.run(propose, critique, repair, stop_check) — pure
                              orchestration, deterministic given deterministic callbacks
    policies.py                  Max-iterations, cost-budget, and convergence stop criteria

  mcp/                     MCP Runtime — protocol adapter in front of Tool Runtime
    domain.py                MCPSession, MCPCapability, MCPToolDescriptor
    session.py                 Session lifecycle + capability negotiation
    bridge.py                   Translates an external MCP server's tool/resource exposure
                               into ToolRegistry registrations (mirrors ProviderModelBridge's
                               "translate, never redesign the target" pattern)
    runtime.py, factory.py, config.py, serializer.py, events.py

  a2a/                     A2A Runtime — agent discovery + cross-agent messaging
    domain.py                AgentCard (capabilities advertisement), A2ATask, A2AMessage
    registry.py                Agent-card discovery registry (local + federated, additive)
    router.py                  Deterministic task routing (mirrors ProviderRouter's
                              strategy-table pattern, reused conceptually, not by import)
    runtime.py, factory.py, config.py, serializer.py, events.py

  multiagent/              Multi-Agent Platform — coordination only
    coordinator.py            Delegation + scheduling across AgentPort.handoff calls
    conflict.py                  Deterministic conflict-resolution policy (verdict-only,
                                same discipline as every Epic 5 PolicyEngine)
    shared_memory.py               A MemoryScope convention for cross-agent shared state —
                                  reuses MemoryPort, adds no new storage

  # ── Developer Platform — extends existing infra, does not replace it ────────
  api/                     (existing) + new routers for tool/skill/agent/workflow/mcp/a2a
  gateway/                 (existing) + new event types for agent/workflow/loop lifecycle
  sdk/                     NEW — generated + hand-maintained client libraries
  cli/                     NEW — a thin wrapper over the SDK + api
  dashboard/               NEW — a web UI over api/gateway/replay (out of this repo's
                           Python platform/ tree; a separate frontend project)
```

**Naming collision check performed:** `platform/pipeline/` (Epic 5) is unrelated to any new
package here; `runtime/` (the legacy M2C.2 voice pipeline) and `runtime/registry.py` (the
legacy capability-provider registry) remain untouched and unrelated — Epic 6 introduces no
package named `runtime` or `registry` at the top level.

## 6. Dependency Graph

```
core, tenant                                              (foundation, unchanged)
  ▲
  │
tool ──────────────▶ core[, tenant]                       (implements ToolPort; no sibling
                                                            Epic 6 runtime import)
skill ─────────────▶ core[, tenant]
hooks ─────────────▶ core[, tenant]                        (a library; consumed by callers,
                                                             imports nothing else)
workflow ──────────▶ core[, tenant]                        (calls out to Tool/Agent/Reasoning
                                                            only through their frozen ports,
                                                            injected — never their packages)
agent ─────────────▶ core[, tenant]                        (consumes ReasoningPort, ToolPort,
                                                            SkillPort — ports only)
loop ──────────────▶ core                                  (a pure library — zero runtime deps)
mcp ────────────────▶ core[, tenant], tool                 (the ONE sanctioned exception:
                                                            mcp translates external tool
                                                            exposure into ToolRegistry
                                                            registrations — same pattern as
                                                            `bridge → provider, reasoning`)
a2a ────────────────▶ core[, tenant], agent                (the other sanctioned exception:
                                                            a2a routes tasks to AgentPort —
                                                            same bridge pattern)
multiagent ─────────▶ core[, tenant], agent, workflow       (a coordinator; composes two
                                                            ports, still no runtime-internal
                                                            imports)

[composition root, extended] ──▶ everything above + all of Epic 5
  (platform/pipeline/ gains new optional stages — Tool/Agent/Workflow — as additive,
   opt-in extensions to RuntimePipeline, never a change to its existing Memory→...→Provider
   contract)
```

**Acyclicity rule, carried forward verbatim:** every new package gets its own
`test_<pkg>_dependency_rules` AST test on day one of its first milestone (not deferred to a
"Phase 2"), correcting the one Epic 5 process gap where a runtime's own dependency test was
sometimes the last file added rather than the first.

## 7. Extension Strategy

Three distinct extension mechanisms, matching what already exists — no new mechanism
invented:

1. **Frozen `core.ports` implementation** (Tool, Workflow, Agent) — identical to how
   Memory/Knowledge/Context/Prompt/Reasoning implemented their M5.0 ports. Zero risk: the
   ports have been stable and unexercised since M5.0.
2. **New additive port + registry** (Skill, MCP, A2A) — identical to how Reasoning added
   `ModelProvider` (not a `core.ports` entry, but a runtime-internal seam) and Provider added
   `AdapterPort`. A new port is warranted when no M5.0 port anticipated the shape (there is no
   `core.ports.skill` or `core.ports.mcp`).
3. **Composition-root bridge** (MCP→Tool, A2A→Agent, Agent↔Reasoning, Workflow↔{Tool,Agent})
   — identical to `ProviderModelBridge`. A bridge is warranted whenever two Epic 6 runtimes
   (or an Epic 6 runtime and Epic 5) would otherwise need to import each other directly.

No fourth mechanism is proposed. This is a deliberate constraint: Epic 5's discipline came
from having exactly one answer to "how does X reach Y," and Epic 6 preserves that.

## 8. Skills Architecture

**Format decision: a versioned manifest (declarative data), not a code package.**

Evaluated options and why each was rejected or accepted:

| Option | Verdict | Why |
|---|---|---|
| Markdown only | ❌ Rejected as the sole format | Human-readable instructions are one *field* of a skill, not the whole shape — a skill also needs a stable id, version, required-capability declarations, and resource references, none of which markdown alone can express deterministically |
| Code package (importable Python) | ❌ Rejected | Violates provider/vendor-neutral, deterministic, sandboxed-execution principles; a skill would become arbitrary code with the trust and security surface that implies, contradicting the security model (§16) |
| Runtime object only (in-memory, no persistence) | ❌ Rejected | Not replay-safe; two runs could see different skills with no audit trail |
| **Manifest + versioned registry (recommended)** | ✅ | A `SkillManifest` (frozen dataclass: `id`, `version`, `instructions: str` [markdown-formatted prose, but as one *field*], `required_capabilities: tuple[str,...]`, `resources: tuple[SkillResource,...]`, `metadata`) resolved through a `SkillRegistry` exactly like Prompt's `TemplateRegistry` resolves templates — content-hashed, versioned, replay-safe, and injectable into a Prompt section or an Agent's plan context without either owning skill semantics |

**Ownership:** Skill Runtime owns resolution/versioning/discovery only. It never executes —
a skill that needs to *do* something declares a `required_capabilities` entry that the Agent
Runtime resolves against the Tool Registry at plan time. This mirrors exactly how Prompt
"renders" without "assembling context" and Context "assembles" without "reasoning" — one
verb, one owner.

**Extension point:** `SkillSource` (mirrors `ContextSource` exactly) — a future org-specific
or marketplace skill source registers additively, with zero Skill Runtime change.

## 9. Hooks Architecture

**Lifecycle hook points** (deterministic, named, ordered): `before_plan`, `after_plan`,
`before_tool_call`, `after_tool_call`, `before_handoff`, `after_handoff`,
`before_workflow_step`, `after_workflow_step`, `on_loop_iteration`, `on_error`. Each point is
a plain string constant (a `HookPoint` StrEnum) — additive; new points are a backward-
compatible enum addition, never a breaking change.

**Hook ownership:** the **Hook Framework** owns the interception mechanism and the
verdict-execution contract; it never owns *what a hook decides* (that's the injected
callback's business, exactly like `ProviderPolicyEngine` decides nothing about *why* a
policy exists, only how a verdict is produced and consumed).

**Execution order:** deterministic, registration-order within a point (mirrors
`SourceSelector`'s "registration order, no scoring" discipline) — never priority-inferred,
never randomized.

**Isolation:** each hook runs in its own try/except; one hook's exception never prevents
sibling hooks at the same point from running (collected, not first-wins), mirroring
`FailoverController`'s "walk the whole list, don't stop at the first problem" shape.

**Failure policy:** a hook that raises produces a deny-by-default verdict for its own vote
(fail-closed for `before_*` gates, fail-open-but-logged for `after_*`/observational points) —
configurable per-point via `HookPolicy`, never hardcoded.

**Security:** hooks never receive raw credentials (the same "no secrets in metadata" rule
from Epic 5's Provider Runtime); a hook that needs to deny a tool call receives the `ToolCall`
shape (typed, already-validated arguments), never an unvalidated raw payload.

**Extension model:** `Hook` is an ABC with one method, `evaluate(event: HookEvent) ->
HookVerdict` — the single simplest extension shape in the whole platform, deliberately, since
hooks are the most likely place third parties will extend the system.

## 10. Workflow Architecture

- **Execution graphs:** `WorkflowGraph` = `WorkflowNode`s + `WorkflowEdge`s, a frozen,
  content-hashed, declarative structure (never code) — satisfying the frozen
  `WorkflowPort`'s own docstring commitment ("declarative graphs, not code-as-intelligence").
- **Branching/conditions:** edges carry an optional deterministic condition expression
  (evaluated against the workflow's own accumulated state — no arbitrary code execution, a
  small, closed expression grammar, mirroring the deliberately-narrow `RoutingContext`
  strategy inputs in Provider's router).
- **Retries:** node-level retry reuses `core.policies.RetryPolicy` verbatim — no new backoff
  math invented.
- **Parallel execution:** fan-out nodes execute their branches independently (each still a
  deterministic call into Tool/Agent/Reasoning); fan-in join is a deterministic barrier, never
  a race — replay must produce the same join order every time, so join order is defined by
  edge-declaration order, not completion order.
- **State:** `WorkflowInstance` is the aggregate — current node, accumulated variables, per-
  node history. Unlike every Epic 5 runtime's repository, **workflow state is durable, not
  temporary** — this is the one deliberate, named exception to the "temporary repository"
  pattern, because a workflow instance must survive a process restart by definition. The
  `core.providers.stores` contracts are reused unchanged; only the configured store's
  `durable` capability flag differs.
- **Persistence & replay:** every state transition is an event through the existing bus,
  observed by the existing `replay/` engine unmodified — Workflow Runtime needs no new replay
  mechanism, only new event types.

## 11. Agent Architecture

- **Lifecycle:** `core.fsm`: `IDLE → PLANNING → ACTING → OBSERVING → (loop back to PLANNING,
  or) DONE | HANDED_OFF | FAILED`.
- **Planning:** the Agent Runtime builds a planning `InferenceRequest` (via the same
  `pipeline.translate`-style pure mapping pattern) and calls `ReasoningRuntime.infer` — **no
  new model integration**; planning *is* an inference call whose prompt happens to include
  tool/skill descriptors.
- **Reasoning orchestration:** entirely delegated to the already-closed Reasoning Runtime;
  Agent Runtime never re-implements streaming/retry/cancellation — it just calls `infer`
  (or `run`) and interprets the `ResponsePackage`'s tool-call chunks.
- **Memory usage:** an `AgentDefinition` carries a `MemoryScope` (reusing
  `core.ports.memory.MemoryScope` unchanged) — the agent's own working memory is a normal
  Memory Runtime tier, not a new storage concept.
- **Workflow integration:** an agent step *can* be a workflow node (`WorkflowNode` invoking
  `AgentPort`) and a workflow *can* be one of an agent's available "tools" conceptually — the
  two compose without either owning the other, exactly per the ownership matrix.
- **Tool usage:** resolved through `ToolRegistry.find`/`ToolPort.invoke` — the agent decides
  *which* tool via its plan; the Tool Runtime decides *whether* it's allowed and *how* it
  executes.
- **Provider usage:** entirely inherited from Reasoning → Provider Runtime → `AdapterPort` —
  the Agent Runtime never selects a provider itself.

## 12. MCP Architecture

- **Architecture:** MCP Runtime is a **protocol adapter**, structurally identical to
  `ProviderModelBridge` — it translates an external MCP server's advertised tools/resources
  into `ToolRegistry.register()` calls, and translates `ToolPort.invoke()` results back into
  MCP protocol responses for the reverse direction (VoxVoice-as-MCP-server, a Phase 2+
  concern).
- **Ownership:** MCP Runtime owns the protocol session and capability negotiation *only*; it
  never executes a tool itself — every invocation still flows through `ToolPort.invoke`, so
  every existing Tool Runtime policy (allow-list, sandbox, retry/timeout) applies uniformly
  whether a tool came from MCP or was registered locally.
- **Session model:** `MCPSession` — capability-negotiated once at connect time, content-
  hashed and events-logged like every other Epic 5/6 session concept (mirrors
  `ReasoningSession`/`ProviderSession`).
- **Tool exposure:** an MCP server's tools appear in `ToolRegistry.find()` exactly like any
  local tool — an agent's plan never needs to know a tool came from MCP.
- **Security:** MCP session credentials (if any) are held by the MCP Runtime's own connection
  object, never surfaced in `ToolSpec`/`ToolCall`/`ToolResult` — the same "no secrets in the
  neutral shape" rule as Provider Runtime's `ProviderMetadata`.

## 13. A2A Architecture

- **Agent discovery:** an `AgentCard` (capabilities, endpoint reference, supported task
  types) is published to an `AgentCardRegistry` — local-first (in-process registry, mirrors
  `ProviderRegistry`'s shape exactly), federated discovery is an explicit, additive Phase 2+
  extension (a `FederatedAgentCardSource`, same pattern as `ContextSource`).
- **Communication:** task-based messaging (`A2ATask`, `A2AMessage`) — deterministic content-
  hash ids, never a raw chat transcript as the wire currency.
- **Capabilities:** advertised via the same `AgentDefinition`/`AgentRef` shape the Agent
  Runtime already owns — A2A does not invent a second capability-description format.
- **Routing:** `A2ARouter` mirrors `ProviderRouter`'s pattern (deterministic strategies over
  a registry snapshot) — reused conceptually, not by import, since A2A routes *tasks to
  agents*, not *requests to providers*, but the "ordered candidate chain, never a single
  irreversible pick" discipline applies identically.
- **Authentication:** peer-agent auth is a `tenant`-seam concern (an `Authorizer` check before
  a task crosses an org boundary) — reuses the exact hook Memory/Knowledge/Context/Prompt/
  Reasoning/Provider tenancy already established; no new auth primitive.

## 14. Dashboard Architecture

The Dashboard is a **presentation-only** consumer of existing and new observability data —
it owns no runtime state:

- **Data sources:** every runtime's `*Statistics`/`PipelineExecutionSnapshot`-style objects
  (already JSON-shaped dataclasses), the `replay/` engine's timeline, and `telemetry/`'s
  metrics/tracing exporters — all pre-existing infrastructure Epic 6 extends with new event
  types, never replaces.
- **Delivery:** through the existing `gateway/` WebSocket streaming seam (new subscription
  topics: `agent.*`, `workflow.*`, `tool.*`, `loop.*`) and the existing `api/` REST routers
  (new routers only — `api/routers/agents.py`, `.../workflows.py`, `.../tools.py`, mirroring
  the existing `api/routers/sessions.py` pattern).
- **Ownership:** the Dashboard (a separate frontend project, out of `platform/`) owns
  rendering only; `api`/`gateway` own the wire contract; every runtime owns its own data.

## 15. SDK Strategy

- **Shape:** one SDK per target language, generated from the same OpenAPI/AsyncAPI contracts
  `api`/`gateway` already expose (or will expose additively) — never hand-rolled per-runtime
  clients that could drift from the actual wire contract.
- **CLI:** a thin wrapper over the SDK, not a second implementation of the wire protocol.
- **Playground:** a thin, disposable client of the SDK for interactive exploration — no
  special-cased backend access.
- **Versioning:** SDK major versions track wire-contract-breaking changes only; every Epic 6
  runtime's own internal version (e.g. `SkillManifest.version`) is unrelated and orthogonal.

## 16. Security Model

Carried forward from Epic 5's own (re-certified at M5.8) security posture, extended to new
surfaces:

- **Credential isolation:** no new runtime's neutral domain shape (`ToolResult`,
  `SkillManifest`, `WorkflowInstance`, `AgentDefinition`, `MCPSession`, `AgentCard`) ever
  carries a raw credential — vendor/peer secrets live only inside the adapter/connection
  object that needs them (MCP session, A2A peer auth), identical to how `ProviderMetadata`
  never carries API keys.
- **Sandboxed tool execution:** `SandboxPort` is a named, frozen interface from Tool Runtime's
  first milestone (not deferred) — arbitrary code execution is explicitly out of scope for
  Phase 1's in-process deterministic executor; a real sandbox (subprocess/container isolation)
  is an additive Phase 2+ adapter behind the same port.
- **Hook-based permission gating:** every tool call passes through `before_tool_call` hooks
  before execution — the Hook Framework is the single place a deny-by-default policy can be
  enforced platform-wide, matching Claude Code's "permission per tool" pattern extracted at
  §3 (as a pattern, not an implementation).
- **Tenant isolation:** every new runtime gets its own tenancy module *from its first
  milestone* (§2's corrected sequencing), reusing the exact `tenant.{Authorizer,
  QuotaEnforcer, AuditSink}` seam every Epic 5 runtime already uses.
- **A2A cross-org auth:** explicit `Authorizer` check before any task crosses an org boundary
  (§13).
- **No dynamic code execution anywhere:** re-affirming the Epic 5 M5.8 finding (zero `eval`/
  `exec`/`pickle`/`subprocess` in the entire stack) as a standing constraint for every new
  Epic 6 package too.

## 17. Migration Plan

No migration is required for Epic 5 — nothing in it changes. The "migration" here is
sequencing:

1. **Phase 0 (this document):** design only. ✅
2. **Phase 1:** Tool Runtime core (implements the already-frozen `ToolPort`; lowest risk,
   since the port and its shapes have been stable since M5.0).
3. **Phase 2:** Skill Runtime + Hook Framework (both additive, both needed before Agent
   Runtime can do anything interesting).
4. **Phase 3:** Loop Engine (a pure library — no runtime dependencies, can land any time
   after Phase 0, but sequenced here because Agent Runtime is its first consumer).
5. **Phase 4:** Agent Runtime core (implements `AgentPort`; consumes Tool + Skill + Loop +
   Reasoning).
6. **Phase 5:** Workflow Runtime core (implements `WorkflowPort`; consumes Tool + Agent +
   Reasoning as node types).
7. **Phase 6:** Intelligence passes for Tool/Skill/Agent/Workflow (policy, tenancy, advanced
   scheduling) — mirroring Epic 5's design→core→intelligence rhythm per runtime.
8. **Phase 7:** MCP Runtime (bridges external tools into the now-stable Tool Runtime).
9. **Phase 8:** A2A Runtime + Multi-Agent Platform (bridges/composes the now-stable Agent
   Runtime).
10. **Phase 9:** Developer Platform expansion (new `api`/`gateway` routers, SDK, CLI,
    Dashboard) — deliberately last, once there is stable surface worth exposing.

Each phase closes independently, exactly like Epic 5's milestones, with its own design doc
(where warranted), core implementation, intelligence pass, and a Decision Log entry.

## 18. ADR List (proposed; none ratified in this phase)

- **ADR-0008 (proposed):** Skills are a versioned manifest, not code (§8's decision,
  elevated — this is exactly the kind of call the Constitution reserves for an ADR).
- **ADR-0009 (proposed):** Workflow state is the platform's first durable (non-temporary)
  repository — establishing the precedent and its criteria (what else, if anything, may ever
  be durable) before a second runtime claims the same exception.
- **ADR-0010 (proposed):** The Hook Framework is a cross-cutting library, not a `core.ports`
  runtime — recording why (§9) so a future contributor doesn't "fix" this by giving it a port.
- **ADR-0011 (proposed):** MCP/A2A are bridge-pattern adapters, never a redesign of
  `ToolPort`/`AgentPort` — recording the boundary explicitly given how much external pressure
  a popular protocol like MCP tends to exert toward "just add MCP-shaped fields to the core
  port."

## 19. Milestone Breakdown

Mirrors Epic 5's own `M{epic}.{n}{A|B-1|B-2}` numbering convention:

| Milestone | Content |
|---|---|
| M6.0 | This document (Phase 0 — Agent Platform architecture) |
| M6.1A / M6.1B-1 / M6.1B-2 | Tool Runtime — design / core / intelligence |
| M6.2A / M6.2B-1 / M6.2B-2 | Skill Runtime — design / core / intelligence |
| M6.3A / M6.3B-1 / M6.3B-2 | Hook Framework — design / core / intelligence |
| M6.4 | Loop Engine (single milestone — it's a small, pure library) |
| M6.5A / M6.5B-1 / M6.5B-2 | Agent Runtime — design / core / intelligence |
| M6.6A / M6.6B-1 / M6.6B-2 | Workflow Runtime — design / core / intelligence |
| M6.7 | Agent Platform Integration (an M5.7-style pipeline extension wiring Tool+Skill+Loop+Agent+Workflow together) |
| M6.8A / M6.8B-1 / M6.8B-2 | MCP Runtime — design / core / intelligence |
| M6.9A / M6.9B-1 / M6.9B-2 | A2A Runtime + Multi-Agent Platform — design / core / intelligence |
| M6.10 | Developer Platform expansion (API/SDK/CLI/Dashboard) |
| M6.11 | Production Hardening, Architecture Certification & Epic 6 Closure (an M5.8-style audit) |

## 20. Risk Analysis

| Risk | Mitigation |
|---|---|
| Agent planning loops never terminate | Loop Engine's stop-check is mandatory, not optional, at the type level (`LoopEngine.run` requires a `stop_check` callback with no default) |
| Skills become a backdoor for arbitrary code | §8's manifest-only decision explicitly forecloses this; enforced by `SkillManifest` having no executable field, only declarative ones |
| MCP/A2A external pressure erodes the port boundary | ADR-0011 names the boundary explicitly before it's tested by a real integration |
| Workflow's durable-repository exception becomes a precedent creep | ADR-0009 states the criteria up front, so the next runtime claiming "I need durable too" has a documented bar to clear |
| Hook Framework becomes a second, competing policy-engine pattern next to every runtime's own `PolicyEngine` | §9 explicitly scopes hooks to *lifecycle interception*, not general policy — each runtime's own `PolicyEngine` (allow-list, quotas, etc.) is unaffected and unreplaced |
| Multi-Agent coordination reinvents a "supervisor" that secretly executes agent logic | §4's ownership matrix explicitly forbids it; the coordinator only calls `AgentPort.handoff` |
| Tenancy-from-milestone-one slows Phase 1 velocity | Accepted trade-off, explicitly named in §2 — Epic 5's own retrofit cost (adding tenancy in every runtime's *second* milestone) is judged higher than paying it up front once |

## 21. Recommendation

**Proceed to M6.1A (Tool Runtime Design)** as the first implementation milestone. Tool
Runtime is the correct starting point: its port has been frozen and stable since M5.0, it has
zero dependencies on any other new Epic 6 package, and every other new runtime (Skill, Agent,
MCP) needs it before it can do anything interesting. This phase (M6.0) produces no code and
changes no file under `platform/{memory,knowledge,context,prompt,reasoning,provider,bridge,
pipeline,core,tenant}/` — Epic 5 remains exactly as certified and tagged at
`epic5-runtime-platform-v1.0`.
