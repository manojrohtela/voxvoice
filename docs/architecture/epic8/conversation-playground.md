# VoxVoice — Conversation Playground (Epic 8, M8.13)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/conversation-playground.md` |
| **Status** | 🟢 **Implemented (M8.13)** — a developer playground for inspecting/debugging/validating Conversation Runtime behaviour through **mock adapters only**. Playground / Mock Adapter / Conversation-flow architecture. |
| **Date** | 2026-07-31 |
| **Scope** | Deterministic mock adapters (LLM/Tool/Voice/Memory); conversation flow (start/continue/reset/close) driven by the mock runtime; turns/messages appended to the reused `ConversationSession`; inspect prompt/variables/tool plan (reuses M8.12 queries) + adapter outputs/timeline/diagnostics; Studio playground; API. **A testing environment — NOT production deployment.** **No external providers, no real LLM API, no real tool execution, no voice synthesis, no network, no new persistence.** **Excluded:** knowledge, RAG, deployment, external tool execution. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ownership change.** All prior 1687 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Conversation Runtime](conversation-runtime.md) · [Agent Builder](agent-builder.md) · [Tool Framework](tool-framework.md) · [Audit & Activity](audit-activity-platform.md) · [Foundation](customer-platform-foundation.md) · Constitution §A2.4/§A5/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

The M8.12 Conversation Runtime produces a deterministic **execution plan** but stops at the
adapter seam (no execution). M8.13 gives developers a **playground** to actually *drive* that plan
— send turns, watch the (mock) LLM respond, see tools "invoked", inspect everything — **without a
single real provider call**. It is a **testing environment**, explicitly not production
deployment.

**Reuse-first, mocks-only.** The playground binds the M8.12 runtime's adapter interfaces to
**deterministic mock implementations** and reuses the published version (M8.10), the resolved
plan + `ConversationSession` (M8.12), the Tool Framework (M8.11), and the durable Audit Platform
(M8.9). It adds **no new persistence** — turns are appended to the existing session's
`ConversationState.history`. No new RBAC scope: playground actions authorize via the existing
workspace-scoped `PROJECT_MANAGE`.

## Mock adapter architecture (`runtime_mocks.py`)

Concrete, **deterministic**, **network-free** implementations of the four M8.12 adapter
interfaces — the *only* adapters the playground binds:

| Mock | Behaviour (pure function of input; no wall clock, no randomness) |
|---|---|
| **`MockLLMAdapter`** | Echoes resolved-prompt metadata + a deterministic token estimate (~4 chars/token) + pseudo-timing; returns a canned text. **Never calls a real provider.** |
| **`MockToolExecutor`** | Returns a deterministic fake output (`mock-output::<tool_id>`) + echoes config/argument keys. **No HTTP/DB/MCP/external system.** |
| **`MockVoiceAdapter`** | Returns synthesized **metadata only** (chars, estimated duration, voice name); `audio` is always `None`. **No audio generated.** |
| **`MockMemoryAdapter`** | A stateless recorder stub (turns persist on the session, not here) — keeps the playground deterministic. |

`MockAdapterSet.default()` bundles them; `.describe()` is the deterministic adapter descriptor
used for the "adapter consistency" check. Identical inputs always yield byte-identical outputs
(`test_mock_adapters_are_deterministic`).

## Playground architecture

`PlaygroundService` (Foundation) drives conversations:

- **Start** reuses `ConversationRuntimeService.create_session` (validates the **published** version
  — a draft never runs — plans deterministically, persists, emits `session.created` +
  `execution.planned`) then emits `conversation.started`.
- **Continue (a turn)** loads the active session, runs the deterministic mocks over the resolved
  plan (LLM over the assembled prompt + user input; the tool executor over each **enabled** plan
  step; voice metadata when the plan requires voice), builds a `ConversationTurn`, and **appends it
  to `ConversationState.history`** (immutable update via `dataclasses.replace`). Emits
  `conversation.turn_planned` + `conversation.turn_completed`.
- **Reset** clears the conversation history while **keeping the resolved plan intact**
  (`conversation.reset`).
- **Close** reuses the runtime close (`session.closed`) and records `conversation.closed`.

The playground **never mutates** the agent's published configuration or its resolved plan — it
only grows conversation history on the session (`test_playground_never_mutates_configuration`).
Because the mocks are pure and the clock is injected, **replaying an identical conversation yields
byte-identical turns** (`test_playground_is_deterministic_replay`).

## Conversation flow

```
Start  → create session (published version, plan resolved)   → conversation.started
Continue(turn) → mock LLM(prompt+input) + mock tools(enabled steps) + mock voice(if required)
              → append ConversationTurn to state.history      → turn_planned + turn_completed
Reset  → clear history, keep plan                             → conversation.reset
Close  → close session                                        → conversation.closed
Inspect → prompt / variables / tool plan (reuse M8.12) + adapter outputs / timeline / diagnostics
```

Each turn yields two chat **messages** (user + assistant mock reply), derived in the view.

## Conversation state (reused, no new persistence)

Turns are stored in the existing `cp_conversation_session.state` JSON (M8.12) — **no new table,
no migration**. Persisted: turns (with mock adapter outputs), derived messages, resolved variables
(from the plan), runtime metadata, diagnostics, and session history. **No vector storage.**

## UI screens

A **Conversation Playground** card (per published agent): **Session Browser** (reuses the runtime
session list), **Chat Window** (message list + input + send), **Prompt Inspector**, **Variable
Inspector**, **Execution Timeline** (turns with tokens/timing/tool counts), **Tool Invocation
Viewer**, **Adapter Output Viewer**, and **Diagnostics Panel** — plus reset/close. Loading/empty/
error states throughout. No business logic in the UI (it calls the playground + reused runtime
API).

## API surface (all additive; reuses `cp_conversation_session`)

```
POST   …/agents/{aid}/playground/conversations                 start (published version only)
GET    …/agents/{aid}/playground/conversations/{sid}           conversation (turns + messages)
POST   …/agents/{aid}/playground/conversations/{sid}/turns     send a turn (mock adapters)
GET    …/agents/{aid}/playground/conversations/{sid}/turns     list turns
GET    …/agents/{aid}/playground/conversations/{sid}/plan      prompt/variables/tool plan (reuse M8.12)
GET    …/agents/{aid}/playground/conversations/{sid}/adapters  mock adapters + latest turn outputs
POST   …/agents/{aid}/playground/conversations/{sid}/reset     reset
POST   …/agents/{aid}/playground/conversations/{sid}/close     close
```

## Layer changes (every Foundation change justified)

- **Foundation (additive, required):** new `runtime_mocks.py` (the four deterministic mock
  adapters + `MockAdapterSet`), `playground_domain.py` (`ConversationTurn`), and `playground.py`
  (`PlaygroundService`); new `TurnView`/`ConversationView` DTOs; additive
  `FoundationEventType.conversation.*`. The factory builds the runtime instance once and **reuses
  it** for both `conversation_runtime` and the `playground`. **Why required:** the mock adapters
  are concrete implementations of the runtime seam and the playground orchestration is domain
  behaviour; both are new. **Why safe:** additive/defaulted; `services.py`, all existing services,
  and the M8.12 runtime are untouched (all 1687 prior tests pass); **no frozen Epic imported**.
- **Application (additive):** new `playground.py` — commands (start/continue/reset/close), queries
  (conversation/turns/adapter-outputs), use cases (one Unit of Work per mutation); prompt/variable/
  tool inspection **reuses** the M8.12 `GetExecutionPlan` query; additive `conversation.*`
  app-event types.
- **Persistence:** **none** — reuses `cp_conversation_session` (turns live in the state JSON). No
  migration.
- **API (additive, thin):** new router under `…/agents/{agent_id}/playground/conversations`.
  Controllers stay transport-only (no-business-logic scan green).
- **Studio (additive):** the Conversation Playground card. No business logic in the UI.

## Testing

`tests/test_conversation_playground.py` — 11 tests: **deterministic mock adapters**, Foundation
unit (**draft never runs**, turn runs mocks + appends history, **deterministic replay**, reset
keeps plan, **config never mutated**, close terminal), API + persistence **Definition-of-Done**
end-to-end (publish → start → multiple turns → inspect prompt/variables/tool-plan/adapter-outputs
→ reset → close), **lifecycle audited** (reusing the M8.9 stream), isolation (cross-org 403, viewer
403), and the Studio playground screens served. **11/11 pass; 1698/1698 repository-wide; ruff
clean; frozen Epics 5/6/7 untouched; every prior M8.2–M8.12 test green.**

## Architecture compliance

- No new ADR, no redesign, no ownership change. **Mock adapters only** — no real provider, no real
  LLM API, no real tool execution, no audio synthesis, no network; the control plane never imports
  a frozen Epic (boundary test green).
- **This is a testing environment, not deployment.** The playground drives the M8.12 plan through
  deterministic mocks; the real Epic 5/6 runtime is out of scope.
- **Reuse-first:** no new RBAC scope; **no new persistence** (reuses the M8.12 session); audit
  reuses M8.9; prompt/variable/tool inspection reuses the M8.12 runtime queries; the runtime
  instance itself is reused. The playground never mutates the published config or the resolved plan.
- Determinism (§A2.4 — pure mocks + injected clock → identical replays), one typed event per fact
  (§A5), org+workspace+project isolation (§A11.2). Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can: publish an agent, start a conversation, send multiple turns, inspect prompts,
variables, tool planning, and adapter outputs, reset the conversation, and close it — using only
the Studio UI + mock runtime, **with no external providers**. Proven by
`test_definition_of_done_end_to_end`, `test_turn_runs_mock_adapters_and_appends_history`,
`test_playground_is_deterministic_replay`, and `test_studio_ui_serves_playground_screens`.

## Known limitations / later hardening

- **Mocks only** — by design. Binding the real Epic 5/6 runtime (behind the ADR-0022 Actuator) is a
  later milestone; the mock adapters share the exact interfaces it will implement.
- **No message-history table / vectors** — turns live in the session state JSON; a dedicated
  message store + `MemoryAdapter` persistence is future work when conversations must scale.
- Read-inspection endpoints follow the existing read pattern (org-scoped by path); per-request
  membership checks remain a cross-cutting hardening item.
- No JS test runner for the Studio playground (verified via served-artifact + API tests).
