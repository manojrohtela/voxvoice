# VoxVoice — Migration Progress (M0–M6)

| | |
|---|---|
| **Document** | `docs/roadmap/migration-progress.md` · **Status:** Living · **Owner:** Technical Owner |
| **Tracks** | Execution of the Constitution migration roadmap (Part B) from prototype → first-party runtime. |
| **Updated** | 2026-07-16 |

| Milestone | Scope | Status |
|---|---|---|
| **M0 — Foundation freeze** | git init, security/.gitignore, ADR extraction, Dograh knowledge archived, baseline snapshot, governance | ✅ Done (tag `foundation-v1`) |
| **M1 — Repo reshape** | Create `core/`; runtime/providers/events/observability skeletons; delete engine-adapter axis; session-start → 503; break `api⇄webhooks` cycle | ✅ Done (branch `chore/m1-repo-reshape`) |
| **M2 — Runtime Plane seed** | Port voicebot prototype into `runtime/` as the first Session Worker; first STT/TTS/LLM/transport plugins; transcripts live via events | ⏳ Next (awaiting approval) |
| **M3 — Streaming & Turn Intelligence** | Streaming STT plugin; endpoint scorer + interrupt engine; latency taps + `metric.turn` (exit: measured p50 ≤ 1.2 s) | ▫ Pending |
| **M4 — Event surface & SDK** | Event Bus impl + outbox + retry/DLQ; WS event gateway; mid-call control API; client SDK | ▫ Pending |
| **M5 — Memory & prompt runtime** | Context builder, rolling summary, entity memory, metadata templating, snapshot/reconnect | ▫ Pending |
| **M6 — Moat & hardening** | Target-language provider plugins + evals; smart-turn EOT; state-machine timeouts; load test; tool runtime | ▫ Pending |

## M1 outcome (2026-07-16)

**Structure now matches Constitution §A14.** New packages: `core/` (contracts,
event types, state defs, errors, ids, provider interfaces), `runtime/` (skeleton:
worker + orchestrator/turn/ai/memory/speech/pipeline), `providers/` (empty
capability homes), `events/` (decoupled webhook dispatcher; bus/outbox at M4),
`observability/` (skeleton).

**Removed** (ADR-0001/0007): `adapters/` (Dograh/LiveKit/factory), the
`/v1/engine/dograh/events` router, the transcript poller (`ingest.py`),
`Agent.engine_workflow_id`, the Dograh webclient harness, engine/Dograh settings.

**Behaviour change (Constitution-mandated, DL-0005):** `POST /v1/sessions`
returns **503** until the Runtime Plane lands at M2. All other endpoints
(agents CRUD, auth, session read/end, transcript) unchanged. Org-scoping preserved
and still tested.

**Quality gates:** 8 tests pass · imports resolve · ruff clean (all packages) ·
zero import-layer violations · `api⇄webhooks` cycle eliminated · doc links resolve.

**Open debt into M2:** `Session.engine` field unused until the runtime sets it;
`voicebot/bot.py` 2 ruff findings (resolved when promoted to a plugin at M2);
orphaned Dograh deploy scripts under `platform/scripts/` (superseded, kept for
Oracle/TURN knowledge).
