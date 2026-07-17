# Runtime Component Spec — Smart Turn Intelligence Engine

| | |
|---|---|
| **Component** | `runtime.smart_turn` (`SmartTurnEngine`, `SmartTurnCoordinator`, `SmartTurnContext`, `SmartTurnDecision`, `SmartTurnPolicy`) + capability `core.providers.semantic_turn.SemanticTurnEvaluator` · **Status:** Implemented (M3.5) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M3.5 — Smart Turn Intelligence Engine (2026-07-17) · **completes Epic 3** |

## Responsibility (single)
Own the **final conversational-timing decision** — combine the sub-frameworks'
outputs (Turn Engine, Voice Activity, Endpoint, Interrupt) + runtime timing + a
configurable adaptive policy + an optional semantic hint into the single
`SmartTurnDecision` the pipeline consumes. It is an **arbiter**: the sub-frameworks
keep owning their own state; Smart Turn only *reads* their decisions and produces a
higher-level one. It does **not** own turn / endpoint / interrupt / voice-activity /
conversation state, and it **never calls an LLM** — any semantic reasoning is behind
the injected `SemanticTurnEvaluator` contract.

## Inputs (neutral signals, never provider objects)
`SmartTurnInput` — a frozen snapshot assembled from the sub-frameworks' *own*
outputs: `endpoint: EndpointDecision`, `interrupt: InterruptDecision`,
`voice_activity: VoiceActivityState`, `turn_phase: TurnPhase`, plus `confidence`,
`assistant_speaking`, `elapsed_frames`. For the semantic evaluator these are reduced
to a **named-float feature map** (`SmartTurnInput.features()`) — no runtime enums or
vendor types cross the capability boundary.

## Ownership (explicit — no overlap)
```
SessionWorker            → worker lifecycle
ConversationOrchestrator → conversation lifecycle; wires Smart Turn when opted-in,
                           resolves the optional semantic evaluator, exposes read-only
TurnEngine               → turn lifecycle
VoiceActivityEngine      → speech activity
EndpointEngine           → endpoint decisions
InterruptEngine          → interruption decisions
SmartTurnEngine          → FINAL conversational-timing decisions (sole writer)
RuntimePipeline          → execution (consumes Smart Turn decisions)
ProviderRegistry         → provider resolution (incl. the SEMANTIC_TURN evaluator)
```
Smart Turn reads the others' decisions; it writes none of their state. The pipeline
still drives each sub-framework directly (they remain independent).

## Decision model
`SmartTurnDecision` (arbitration outputs, not a lifecycle FSM — no transition
table):
| Decision | Meaning |
|---|---|
| `WAIT` | hold; not enough signal yet (e.g. within adaptive grace / smoothing) |
| `CONTINUE_LISTENING` | the user turn is still open |
| `FINISH_TURN` | the user turn is complete → respond now |
| `CANCEL_ENDPOINT` | withdraw a pending endpoint (user resumed) |
| `DELAY_RESPONSE` | endpoint reached, hold the reply for the adaptive silence |
| `CONFIRM_INTERRUPT` | a barge-in should pre-empt the assistant |
| `IGNORE_INTERRUPT` | a barge-in is below threshold → keep speaking |
| `RESUME_ASSISTANT` | a false/withdrawn interrupt → resume playback |

`FINISH_DECISIONS = {FINISH_TURN, DELAY_RESPONSE}` and
`PREEMPT_DECISIONS = {CONFIRM_INTERRUPT}` are what the pipeline acts on.

**Conflict resolution (deterministic, documented):** a confirmed interrupt takes
precedence over a simultaneous endpoint (barge-in is urgent). New decisions are
additive (§A12) and require no change to existing arbitration.

## Policy model (adaptive — nothing hardcoded)
`SmartTurnPolicy` (frame counts = observations; validated at construction —
negative values raise `InvalidSmartTurnPolicy`):
| Knob | Meaning | Default |
|---|---|---|
| `adaptive_silence_frames` / `adaptive_silence_scale` | post-endpoint hold before responding (>0 → DELAY_RESPONSE) | 0 / 1.0 |
| `adaptive_grace_frames` | early-endpoint frames that resolve to WAIT before finishing | 0 |
| `dynamic_interruption_threshold` | min weighted confidence to CONFIRM a barge-in | 0.0 |
| `speech_confidence_weight` | multiplier on raw confidence | 1.0 |
| `timing_smoothing_frames` | consecutive identical raw decisions before a *changed* decision commits (WAIT bridges) | 0 |
| `semantic_weight` | how strongly a semantic hint is blended (0 → ignored) | 1.0 |

The **defaults are the identity policy** → endpoint-confirmed = `FINISH_TURN`,
confirmed interrupt = `CONFIRM_INTERRUPT`: byte-for-byte the pre-M3.5 behaviour. All
adaptation is opt-in. The adaptive hold for a `DELAY_RESPONSE` is exposed as
`pending_delay_frames` for a real transport scheduler; the in-process profile
records it (deterministic, no wall-clock sleep — DL-0016).

## Semantic extension interface (no runtime change for future models)
`core.providers.semantic_turn.SemanticTurnEvaluator` — a **registry-resolved
capability** (`CapabilityKind.SEMANTIC_TURN`). `evaluate(features: Mapping[str,
float]) -> SemanticHint` returns neutral float hints (`end_of_turn_confidence`,
`interrupt_intent`, `delay_scale`), blended per `semantic_weight`. A future semantic
module (EOT classifier, intent model, LLM-backed service) implements this contract
and registers — the orchestrator resolves it automatically, **no runtime code
changes** and full vendor neutrality (Constitution §A6/§A12). The runtime never
calls an LLM; it calls the contract. The default is `NullSemanticEvaluator`
(identity). `evaluate` is synchronous/deterministic; async or remote reasoning runs
out-of-band and is surfaced as features/hints. A misbehaving evaluator degrades to
the identity hint (logged) — it never breaks the deterministic core.

## Runtime integration (opt-in; graceful fallback)
The Orchestrator wires Smart Turn **only when the agent opts in**
(`AgentSpec.smart_turn`), passing `AgentSpec.smart_turn_policy` and any resolved
`SEMANTIC_TURN` evaluator. When active, the pipeline routes its two decision points
through Smart Turn:
- **turn completion** — `_endpoint_finishes(endpoint_decision)` asks
  `on_endpoint(...)`; it responds iff the decision is in `FINISH_DECISIONS`.
- **barge-in** (interruptible path) — `_interrupt_preempts()` asks `on_interrupt(...)`;
  it pre-empts iff `CONFIRM_INTERRUPT`, else `RESUME_ASSISTANT` keeps the assistant
  speaking (the interrupt is cleared and monitoring re-armed).
When not wired, the pipeline consumes the raw endpoint/interrupt decisions exactly
as M3.4 (graceful fallback; verified — the prior 202 tests are unchanged).

## Failure handling (no silent recovery)
- **conflicting decisions** → fixed documented precedence (interrupt > endpoint) —
  deterministic, not an error.
- **invalid policy combinations** → `InvalidSmartTurnPolicy` at construction (rooted
  in `core.errors.RuntimeConflict`).
- **duplicate/idempotent inputs** → the engine is a pure function of the snapshot;
  identical inputs yield identical decisions (deterministic replay).
- **misbehaving semantic evaluator** → degrades to the identity hint (logged); the
  deterministic decision still stands.
- **runtime shutdown / cancellation** → `cancel()`/`reset()` clears smoothing/adaptive
  working state; the pipeline's `stop()` calls it.

## Timing captured (in `SmartTurnContext.timing`)
Latest entry timestamp per decision; `last_decision` + `decision_count` for
inspection. Captured only — no analytics processing.

## Dependencies & boundaries
`runtime.smart_turn` imports `core.providers.semantic_turn` (the capability contract)
and the sibling runtime frameworks' *decision enums* (`endpoint`, `interrupt`,
`turn`, `voice_activity`) + stdlib. Never imports `api`, the pipeline substrate, or a
provider implementation. `core.providers.semantic_turn` imports stdlib only (no
runtime). Consumed by the pipeline (via `SmartTurnCoordinator`) and the orchestrator.

## Extension points (beyond Epic 3)
Real semantic EOT / intent models register a `SEMANTIC_TURN` provider · richer
adaptive policies (prosody-scaled grace/hold) are new `SmartTurnPolicy` fields ·
Smart Turn events feed metrics / the Event Bus (M4) · a reinforcement-tuned policy
is a drop-in engine behind the same coordinator. All additive — no runtime change.
