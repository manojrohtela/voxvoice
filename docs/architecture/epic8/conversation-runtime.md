# VoxVoice — Conversation Runtime (Epic 8, M8.12)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/conversation-runtime.md` |
| **Status** | 🟢 **Implemented (M8.12)** — the execution/orchestration plane that runs a **Published Agent Version** to a deterministic **execution plan**. Runtime pipeline / session lifecycle / prompt assembly / tool resolution / adapter interfaces. |
| **Date** | 2026-07-31 |
| **Scope** | Conversation session lifecycle (create/resume/close/expire + metadata/correlation ids); conversation state (resolved prompt/variables/tool plan/metadata); the deterministic pipeline (load published version → resolve variables → build prompt → resolve tool registry → execution plan); variable precedence; adapter **interfaces only** (LLM/Voice/Tool/Memory); execution result (resolved prompt/variables/tool plan/timing/diagnostics); persistence of session + state; Studio Runtime Inspector; API. **No LLM, no tool execution, no voice, no real provider, no message history, no vectors, no deployment, no playground.** **Excluded:** conversation playground, knowledge retrieval, RAG. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ownership change.** All prior 1676 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Agent Builder](agent-builder.md) · [Tool Framework](tool-framework.md) · [Audit & Activity](audit-activity-platform.md) · [Project](project-lifecycle.md) Lifecycle · [Foundation](customer-platform-foundation.md) · [Persistence](customer-platform-persistence-layer.md) · Constitution §A2.4/§A2.6/§A5/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

A published Agent + its attached Tools describe *what* an agent is; the Conversation Runtime is
*how it runs*. M8.12 implements the **orchestration** half: it takes an immutable **Published
Agent Version**, resolves its variables and prompt, resolves its tool plan, and produces an
**execution plan** — deterministically, with **no LLM call, no tool execution, and no voice
synthesis**. The actual execution (via the frozen Epic 5 Runtime Platform / Epic 6 Execution
Platform) plugs in later through the **adapter interfaces** this milestone defines.

**Reuse without breaking the boundary.** ADR-0021 forbids the control plane from importing the
frozen runtime, and the dependency tests enforce it. So "reuse Runtime/Execution Platform" is
**reuse-by-interface**: the runtime consumes the published version (M8.10), the Tool Framework
(M8.11) and the durable Audit Platform (M8.9) that already live in the control plane, and it
declares the `LLMAdapter`/`VoiceAdapter`/`ToolExecutor`/`MemoryAdapter` **contracts** the frozen
runtime will satisfy — it never imports or instantiates them. No new RBAC scope: session
mutations authorize via the existing workspace-scoped `PROJECT_MANAGE`.

## Golden rules (encoded + tested)

- **Only a published version executes.** An agent with `published_version == 0` (a draft) raises
  on `create_session` (`test_draft_never_executes`).
- **The runtime never mutates configuration or history** — the pipeline is pure over the
  published snapshot; the agent/tool/version rows are unchanged (`test_runtime_never_mutates_configuration`).
- **Deterministic** — identical inputs yield an identical plan (`test_pipeline_is_deterministic`).
- **Stateless except explicit `ConversationState`** — the persisted state is the only thing
  carried across resume; a fresh process resumes from it (`test_session_survives_restart_and_resumes`).

## Runtime architecture (the pipeline)

`ConversationRuntimeService._plan` is a pure, deterministic function:

```
Published Version (immutable snapshot)
   → Resolve Variables (precedence)
   → Resolve Tool Registry (ordering/enable/override/deps)
   → Build Prompt (deterministic assembly, secrets masked)
   → Conversation State (resolved prompt + variables + tool plan + metadata + timing + diagnostics)
   → Execution Plan (tool steps + required adapter interfaces)
```

No step calls an LLM, executes a tool, or synthesizes voice. The `timing` is taken from the
injected `Clock` (deterministic under `FixedClock`); diagnostics are canonically ordered.

## Session lifecycle

`ConversationSession` (bound to one immutable `agent_version`) has states
`active`/`closed`/`expired`. `create_session` validates the published version exists, plans, and
persists an `active` session (emitting `session.created` + `execution.planned`); `resume_session`
re-touches an active session (`session.resumed`); `close_session`/`expire_session` are terminal
(`session.closed`/`session.expired`); a planning failure emits `execution.failed` and re-raises.
Resuming or closing a non-active session is a `ConflictError`. Correlation ids + created/updated
timestamps + free-form metadata are carried on the session.

## Variable resolution

Four-tier precedence, **later overrides earlier** (immutable during execution):

```
System Defaults  (timezone, locale)
   ↓ Project Variables   (project.metadata["variables"])
   ↓ Agent Variables     (published version snapshot config.variables; secrets flagged)
   ↓ Runtime Variables   (provided at session creation — highest precedence)
```

Each `ResolvedVariable` records its winning `source`; the resolved set is sorted by name
(deterministic). Secret agent variables keep their value internally (for the future runtime) but
are **redacted at every read boundary** and **masked (`***`) in the assembled prompt** — no
inspector ever leaks a secret (`test_variable_precedence_and_secret_masking`).

## Prompt assembly

Deterministic assembly of the published version's `system_prompt` / `persona` / `instructions` /
`conversation_style` + a variables block + (empty) history block + a tool-context block (enabled
tool names/categories) + the greeting. `{{variable}}` references are substituted from the
resolved values (secrets → `***`; unresolved → a diagnostic, left literal). **No prompt is
executed.** Sections are kept separate for the inspector plus a single assembled `text`.

## Tool resolution

The agent's `AgentTool` attachments are resolved into an ordered `ExecutionPlan`, respecting
**ordering** (position), **enable flags** (attachment.enabled ∧ tool.enabled ∧ not archived),
**config overrides** (tool settings merged with the per-agent override) and **dependencies**
(`depends_on` carried through). A missing/deleted tool → an error diagnostic; an archived/disabled
tool → a disabled step + a diagnostic. The plan also declares the **required adapter interfaces**
(`llm` always; `voice` iff the version enables voice; `tool_executor` iff any enabled step;
`memory` false in M8.12). **The plan is returned, never executed** (`test_tool_resolution_ordering_enable_and_adapters`).

## Adapter interfaces (interfaces only)

`runtime_adapters.py` defines `LLMAdapter` / `VoiceAdapter` / `ToolExecutor` / `MemoryAdapter` as
abstract contracts with **no implementations, no provider SDKs, no I/O** — the seam the frozen
Epic 5/6 runtime satisfies later. Keeping them in the control-plane Foundation (rather than
importing the frozen runtime) preserves the dependency boundary while giving the runtime a typed
contract.

## Execution result

The inspector returns: **resolved prompt** (sections + text), **resolved variables** (with source,
secrets redacted), **resolved tool plan** (ordered steps + required adapters), **execution
metadata** (agent id, version, immutability), **timing**, and **diagnostics** — **no model
response** (there is no model call).

## Layer changes (every Foundation change justified)

- **Foundation (additive, required):** new `runtime_domain.py` (`ConversationSession`/
  `ConversationState` + `ResolvedPrompt`/`ResolvedVariable`/`ExecutionPlan`/`ToolPlanStep` + enums),
  `runtime_adapters.py` (the four adapter ABCs), and `runtime.py` (`ConversationRuntimeService` +
  the deterministic pipeline); new `ConversationSessionRepository` (+ in-memory); new `SessionView`/
  `SessionSummaryView`; additive `FoundationEventType.session.*`/`execution.*`, id prefix (`cvs`);
  facade + factory gain `conversation_runtime` + one defaulted repo param. **Why required:** a
  runtime session + its state are new control-plane aggregates; the orchestration is domain
  behaviour that belongs in the Foundation. **Why safe:** additive/defaulted; `services.py` and all
  existing services untouched (all 1676 prior tests pass); **no frozen Epic imported** (boundary
  test green).
- **Application (additive):** new `runtime.py` — commands, queries (state/plan inspection), use
  cases (one Unit of Work per session mutation so the audit rows commit atomically), registered
  into the existing buses; additive `session.*`/`execution.planned` app-event types.
- **Persistence (additive):** new `cp_conversation_session` table (session + explicit state as
  portable JSON — **no message-history table, no vectors**), mapping, `SqlConversationSessionRepository`,
  **migration 0008** (create-table only), composition wiring.
- **API (additive, thin):** new router under `…/agents/{agent_id}/sessions`
  (create/list/get + resume/close + state + plan). Controllers stay transport-only (verified by the
  no-business-logic scan — refined to `.session` so the domain noun "session" no longer false-positives; the scan still catches any real DB-session access).
- **Studio (additive):** a Runtime Inspector — Session viewer, Execution Plan viewer, Variable
  Inspector, Tool Plan viewer, Diagnostics. **No chat interface.** No business logic in the UI.

## API surface (all additive)

```
POST   …/agents/{aid}/sessions                      create session (published version only)
GET    …/agents/{aid}/sessions                      list sessions
GET    …/agents/{aid}/sessions/{sid}                get session (state redacted)
POST   …/agents/{aid}/sessions/{sid}/resume         resume
POST   …/agents/{aid}/sessions/{sid}/close          close
GET    …/agents/{aid}/sessions/{sid}/state          resolved runtime state
GET    …/agents/{aid}/sessions/{sid}/plan           execution plan (prompt/vars/tools/timing/diagnostics)
```

## Testing

`tests/test_conversation_runtime.py` — 11 tests: Foundation unit (**draft never executes**,
variable precedence + secret masking, tool resolution ordering/enable/adapters, **determinism**,
session lifecycle + resume/close, **runtime never mutates config**), API + persistence
**Definition-of-Done** end-to-end (publish → create session → inspect prompt/variables/tools/state
→ resume → close), **session + planning audited** (reusing the M8.9 stream), **session survives
restart and resumes** over a file DB, isolation (cross-org create 403, viewer 403), and the Studio
runtime screens served. **11/11 pass; 1687/1687 repository-wide; ruff clean; frozen Epics 5/6/7
untouched; every prior M8.2–M8.11 test green.**

## Architecture compliance

- No new ADR, no redesign, no ownership change. The runtime plans; it never executes, never calls
  an LLM, never synthesizes voice, and **never imports a frozen Epic** (dependency-boundary test
  green) — the adapter interfaces are the seam onto Epic 5/6.
- **Golden rules enforced:** only a published version runs; the runtime never mutates config or
  history; deterministic; stateless except the explicit persisted `ConversationState`.
- **Reuse-first:** no new RBAC scope; audit reuses M8.9; the published version (M8.10) + tool
  framework (M8.11) are the inputs. The Foundation grew only where a new domain required it.
- Determinism (§A2.4), fail-fast validation (§A2.6), one typed event per fact (§A5),
  org+workspace+project isolation (§A11.2). Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can: publish an agent, create a runtime session, inspect the fully resolved prompt,
resolved variables, tool resolution, and runtime state, resume the session, and close it — using
only the Studio UI + Control Plane API, **with no LLM call, no tool execution, no voice**. Proven
by `test_definition_of_done_end_to_end`, `test_variable_precedence_and_secret_masking`,
`test_tool_resolution_ordering_enable_and_adapters`, and `test_session_survives_restart_and_resumes`.

## Known limitations / later hardening

- **No execution** — by design. The runtime produces a plan; the frozen Epic 5/6 runtime binds the
  adapter interfaces and executes later.
- **No message history / no vectors** — conversation history is modelled but empty (M8.12 persists
  no turns); the `MemoryAdapter` is the future seam.
- **PROJECT-granular RBAC** still deferred (from M8.8) — session mutations are workspace-scoped;
  read-inspection endpoints follow the existing read pattern (org-scoped by path, no per-request
  membership check) — a cross-cutting hardening item for all read endpoints.
- No JS test runner for the Studio runtime inspector (verified via served-artifact + API tests).
