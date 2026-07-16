# Runtime Component Spec — Conversation State Machine

| | |
|---|---|
| **Component** | `runtime.conversation.ConversationStateMachine` · **Status:** Implemented (M2B.1) |
| **State definitions** | `core.states.SessionState` + `SESSION_TRANSITIONS` + `SESSION_STATE_META` (single source of truth) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M2B.1 — Conversation State Machine (2026-07-16) |

## Responsibility (single)
Be the **deterministic, single source of truth for a conversation's lifecycle**:
legal states, validated transitions, guards, per-state metadata, immutable
transition history, typed transition errors, and synchronous transition
notifications. **Not** the Conversation Orchestrator — no orchestration, providers,
AI, streaming, memory, or I/O. Future milestones consume this machine instead of
tracking conversation state themselves.

## States (= Constitution `SessionState`; §A4.4, §A0)
A *Session is a conversation instance* (glossary §A0), so `SessionState` **is** the
conversation lifecycle. Each state has metadata (`SESSION_STATE_META`):

| State | Description | Terminal | Recovery policy |
|---|---|---|---|
| `CREATED` | Session created; no transport yet. | no | On failure → FAILED |
| `CONNECTED` | Transport connected; greeting/first audio pending. | no | On failure → FAILED |
| `IN_CONVERSATION` | Active conversation; the **turn machine** (`TurnState`) runs within. | no | Transport drop → SUSPENDED; end → ENDING |
| `SUSPENDED` | Transport dropped; within reconnect grace (recoverable). | no | Resume → IN_CONVERSATION; grace expiry → ENDING |
| `ENDING` | Teardown in progress (flush, snapshot, close). | no | Proceeds to ENDED |
| `ENDED` | Terminal: ended (reason on the transition: completed \| cancelled \| …). | **yes** | None |
| `FAILED` | Terminal: unrecoverable failure. | **yes** | None (host discards) |

## Transition diagram
```
        ┌──────────────────────────── FAILED (terminal)
        │                               ▲       ▲
   CREATED ──► CONNECTED ──► IN_CONVERSATION ──► ENDING ──► ENDED (terminal)
                                  ▲   │
                          (resume)│   │(transport drop)
                                  │   ▼
                               SUSPENDED ──► ENDING
```
Transition table: `core.states.SESSION_TRANSITIONS`. Illegal moves raise; terminal
states are immutable; self-transitions (duplicates) are rejected.

## Reconciliation with the M2B.1 example states (DL-0007)
The milestone listed example states blending session- and turn-level phases. To
honour "one concept, one name" and "do not modify the Constitution", they map onto
the existing model rather than a new divergent enum:

| M2B.1 example | This model |
|---|---|
| Created | `CREATED` |
| WaitingForAudio | `CONNECTED` |
| Listening / Processing / Speaking / Interrupted | **turn phases** → `core.states.TurnState` (LISTENING/THINKING/SPEAKING + barge-in), driven by Turn Intelligence at **M3** — not conversation-lifecycle states (M2B.1 non-goal: turn detection) |
| Completed | `ENDED` with `reason="completed"` |
| Cancelled | `ENDED` with `reason="cancelled"` |
| Failed | `FAILED` |

End-*reason* (completed/cancelled/…) is carried on the transition (Constitution §A5
`session.ended {reason}`), not modelled as separate states.

## Inputs / Outputs
- **Inputs:** `transition(target, *, reason, source)` — explicit, validated.
  Construction takes an optional `on_transition` observer and an optional `clock`.
- **Outputs:** `TransitionRecord` per transition; `state`, `is_terminal`,
  `history` (immutable tuple), `metadata()`, `allowed_transitions()`,
  `can_transition()`; observer notifications.

## Transition history
Immutable `TransitionRecord`s (frozen): `previous` (None only for initial
CREATED), `new`, `ts`, `reason`, `source`. `history` returns a tuple snapshot;
past records are never mutated. Persistence is **not** implemented here (the event
log / snapshots are M4/M5).

## Validation & errors (typed, under `core.errors.RuntimeConflict`)
| Rule | Error |
|---|---|
| Target not legal from current state | `InvalidConversationTransition` |
| Transition out of a terminal state | `TerminalConversationError` |
| Transition into the already-active state | `DuplicateConversationTransition` |

No silent corrections; no implicit transitions. Deterministic: the same call
sequence always yields the same transitions and history.

## Observability
Synchronous `on_transition(TransitionRecord)` + structured logs. This is **not**
the Event Bus (M4); a bridge can later forward these records as `core.events`
events. An observer that raises is swallowed and logged — it never breaks the
machine.

## Dependencies & boundaries
Imports `core.states` (definitions) and `runtime.errors`. Pure logic — no async,
no I/O, no providers, no `api`. Consumed by the Runtime Plane (the Orchestrator at
M2B.2).

## Not in scope (later milestones)
Orchestration / wiring to the worker (M2B.2) · turn machine execution (M3) · Event
Bus emission (M4) · history persistence / snapshots (M4/M5).
