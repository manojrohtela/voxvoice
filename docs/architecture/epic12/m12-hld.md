# Epic 12 — Developer Platform: High-Level Design

| | |
|---|---|
| **Status** | Living (M12.1) |
| **Date** | 2026-09-21 |
| **Scope** | Epic 12 as a whole: architecture map, reuse inventory, gaps, HLD, M11/Epic 10 boundaries, risks, compatibility |
| **Governing decision** | [ADR-0055](../../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) |
| **Related** | [Product vision](m12-product-vision.md) · [LLD](m12-lld.md) · [DB design](m12-db-design.md) · [API design](../../apis/m12-api-design.md) · [Frontend](m12-frontend-architecture.md) · [Local dev](m12-local-development.md) · [Security model](../../security/m12-security-model.md) · [Plan](../../roadmap/epic12-implementation-plan.md) |

Every fact in §1–§3 was established by reading or running the repository at
`d5ae2c1` (`m11-local-baseline`) on 2026-09-21. Nothing here is carried over from an earlier
session's description.

## 1. Baseline verified

| | |
|---|---|
| Branch / HEAD | `feat/m10.2-durable-event-foundation` @ `d5ae2c151bcb7db7be64fb184a3272251fb8da61`, in sync with `origin` |
| `m11-local-baseline` | annotated tag → `d5ae2c1` (development checkpoint, not a release) |
| `v5.0.0` | → `3779e93`; `main` → `c3c7397`; no `v6*` tag |
| Tree | clean |
| Tooling | Python 3.13.3, `uv` 0.9.30 (`uv lock --check`: 53 packages), Node 24.9.0 / npm 11.6.0 |

## 2. Current architecture map

```
platform/
├── api/                     CONTROL PLANE — every file present at v4.0.0 is FROZEN (E10-7)
│   ├── main.py              M11 default app: /v1/agents, /v1/sessions, /health (loopback)
│   ├── models.py            org · apikey · agent · session · transcript   ← THE RUNTIME MODEL
│   ├── security.py          x-api-key → Org (sha256-hashed keys)
│   ├── routers/{agents,sessions}.py      developer API v1 (frozen)
│   ├── routers/webrtc.py    M11.16.4 (added): rendezvous + /v1/webrtc/sessions → calls create_session
│   ├── webrtc/              M11.16.4 (added): build_webrtc_app, RendezvousRegistry, client.html
│   ├── metering/            Epic 10 (added): MetricTurnBuffer (in-memory, not durable)
│   ├── customer/ foundation/ application/ persistence/ adapters/
│   │                        EPIC 8 customer platform: cp_* tables, JWT (no credential store),
│   │                        RBAC, audit, versioned agents, Studio (vanilla JS) — MOCK runtime only
├── bootstrap.py             composition root: build_session_controller, REAL_PROVIDER_PROFILE,
│                            build_real_session_controller, build_observability_plane
├── core/                    contracts, events, SessionController seam
├── runtime/                 RUNTIME PLANE (M11): RuntimeSessionService, orchestrator, pipeline,
│                            turn/endpoint/interrupt/VAD, speech (clauses, scheduler, playback), faults
├── providers/               Deepgram · Silero · Groq · Cartesia · aiortc WebRTC transport
├── events/                  Epic 10 durable spine: evt_event · evt_outbox · evt_outbox_failure
├── observability/           TurnLatencyTap — sole producer of metric.turn
├── billing/                 Epic 9 (frozen; not touched by Epic 12)
└── product/, agent/, …      Epic 5–7 runtimes (not on the voice product path)
```

**The live product path today** (verified by DL-0234's first real conversation):

```
browser (api/webrtc/client.html, org API key pasted by operator)
  → POST /v1/webrtc/rendezvous (x-api-key)          → RendezvousRegistry.reserve(org)
  → POST /v1/webrtc/sessions   (x-api-key)          → create_session (frozen) → SessionController
  ↔ GET offer / POST answer    (x-signalling-token) → aiortc peer
  → RuntimeSessionService (REAL_PROVIDER_PROFILE) → Deepgram → Silero → Groq → Cartesia → playback
  → transcripts: FINAL items → `transcript` table (ts is always 0.0)
```

### 2.1 Answers to the fifteen reconnaissance questions

| # | Question | Finding |
|---|---|---|
| 1 | Frontend technology | None as an application. Two static pages: `api/webrtc/client.html` (6 KB, operator client) and Epic 8 Studio `api/customer/studio/index.html` (105 KB vanilla JS). Billing has its own static studio pages. |
| 2 | A frontend application? | No. No `package.json` anywhere in the repo. |
| 3 | Backend API surface | M11 developer API (`/v1/agents`, `/v1/sessions`, `/v1/webrtc/*`, `/health`), and the Epic 8 control-plane API (`/v1/auth`, orgs, workspaces, projects, agent builder, playground, observability, …). The Epic 8 API is served only by `api.customer.app:create_app`, which has no documented runtime entrypoint. |
| 4 | Database models | M11: 5 tables via `SQLModel.metadata.create_all` (no migrations). Epic 8: 20+ `cp_*` tables, migrations `0001`–`0012`. Billing `0013`–`0033`. Events `0034`–`0036` (`evt_*`). |
| 5 | Authentication | M11: hashed org API keys, no users. Epic 8: JWT with dev-only token issuance and no credential verification. |
| 6 | Tenancy | M11 `org` (every M11 query is org-scoped; cross-org reads return 404). Epic 8 `cp_organization → cp_workspace → cp_project`. |
| 7 | Agent model | M11 `agent` (flat: prompt, first_message, language, llm_model, voice, flags). Epic 8 `cp_agent` (nested JSON config, draft/publish versions). Only M11 `agent` reaches the runtime. |
| 8 | Session model | M11 `session` (created/active/ended/failed, org_id, agent_id, timestamps). Epic 8 `cp_conversation_session` (mock). |
| 9 | Conversation/event data | `transcript` (final items only; `ts` = 0.0). `evt_event` durable log (`session_id`, `org_id`, `seq`, `type`, `data`) exists but is **not wired** in the local WebRTC app (§8, F-2). `metric.turn` is produced by `TurnLatencyTap` only when an `ObservabilityPlane` is wired. |
| 10 | Reusable directly | See §3. |
| 11 | Must be extended | Composition (spine wiring, controller decoration), not frozen code. |
| 12 | Genuinely new | Users + credentials, memberships, console sessions, API-key metadata, the console API, the console app, the SDK. |
| 13 | Belongs to M12 | Identity, product surfaces, read projections, SDK, console. |
| 14 | Stays in M11 | Everything from WebRTC transport to playback, ProviderGuard/Registry, CredentialProvider, REAL_PROVIDER_PROFILE, Stage-2 deployment. |
| 15 | Frozen by Epic 10 | `events/**` (as released), `observability/turn_latency.py`, `api/metering/**`, `metric.turn` semantics, migrations ≤ `0036`, every pre-`v4.0.0` `api/` file (E10-7), `api/**observability*` (E10-9). |

## 3. Reusable components

| Component | Reuse in Epic 12 |
|---|---|
| `build_webrtc_app` | Wrapped by the console app (DP3). Provides lifespan, controller, registry, `/health`, developer API. |
| `api.routers.sessions.create_session` / `end_session` | Called as functions by the Playground (DP5). |
| `api.routers.webrtc.create_webrtc_session`, `RendezvousRegistry.reserve` | Called by the Playground; the browser keeps using `/v1/webrtc/rendezvous/*` with its signalling token. |
| `api.security.generate_api_key` / `hash_key` | API-key creation from the console (M12.7). |
| `apikey` table + `get_org` | Developer API auth, unchanged. Revocation = delete the row, so the frozen `get_org` rejects it immediately. |
| `agent`, `session`, `transcript` tables | The product's agent, session and transcript facts. |
| `evt_event` via `DurableEventLog.read_session` | Conversation event timelines and turn metrics. |
| `build_observability_plane(durable=…)` | Wires the spine at composition (M12.5), as ADR-0050 SW2 prescribes. |
| `api/webrtc/client.html` signalling logic | Ported to the TypeScript SDK core (M12.4) and then used by the Playground. |
| Epic 8 error envelope `{error:{type,message,request_id}}` | Reused as the console API error shape. |

## 4. Missing product capabilities

Login/signup/logout · user identity with credentials · organization membership and roles · a
dashboard · console-side agent management with honest field validation · a Playground that needs
no API key in the browser · conversation list/detail · analytics over real signals · API-key
lifecycle (list, create with one-time reveal, revoke, last-used) · developer docs and quickstart ·
TypeScript SDK · a runnable console app.

## 5. High-level design

```
                ┌─────────────────────────── browser ───────────────────────────┐
                │  console/  (React + TS)                                        │
                │   Overview · Agents · Playground · Conversations · Analytics · │
                │   API keys & SDK · Settings        uses sdk/ core for WebRTC   │
                └──────┬──────────────── same origin ─────────────────┬──────────┘
      cookie session   │ /v1/console/*                     signalling │ /v1/webrtc/rendezvous/*
                       ▼                                              ▼   (x-signalling-token)
┌────────────────────────── one process: build_console_app ─────────────────────────────┐
│  api/console/  (NEW, additive)                     build_webrtc_app (M11, wrapped)     │
│   identity · orgs · agents · playground ·           /v1/agents  /v1/sessions  (x-api-key)
│   conversations · analytics · api-keys · meta       /v1/webrtc/*  /health              │
│        │ calls frozen functions (DP5)                       │                          │
│        └──────────────► create_webrtc_session → create_session → SessionController ────┼──► RUNTIME PLANE (M11)
│   reads (DP6)                                                                          │     unchanged
│   dp_* tables ─┐   org · apikey · agent · session · transcript   evt_event (Epic 10)   │
└────────────────┴──────────────────────── System of Record ─────────────────────────────┘
      developer's backend ── x-api-key ──► /v1/agents, /v1/sessions, /v1/webrtc/*   (SDK server helper)
```

**Principles.** One process locally (§A9 Stage 1). One System of Record. The console owns only
`dp_*` rows and UI; everything else is requested from, or projected out of, its owner (ADR-0021).
Every path to the runtime goes through the frozen `create_session`.

## 6. M11 integration boundary

Epic 12 **does not modify**: WebRTC transport, STT, VAD, endpointing, Turn Engine, LLM streaming,
clause pipeline, TTS, Playback Engine, ProviderGuard, ProviderRegistry, CredentialProvider,
`REAL_PROVIDER_PROFILE`, `create_session`, the rendezvous protocol, or `client.html`.

Epic 12 **may, by addition only**:

* wrap `build_webrtc_app` (M12.1);
* decorate the `SessionController` at the composition root. For example, F-1 could be fixed by a
  controller that fills `allow_interruptions` from the agent row, if M12.3 chooses that fix under
  its own ADR;
* pass an `ObservabilityPlane` into the controller build (M12.5). This needs a small, tested,
  opt-in parameter on the post-`v4.0.0` `api/webrtc/app.py` or on `bootstrap.py`, justified in
  that milestone.

Any genuinely required M11 change is isolated, explained, tested and kept contract-preserving.

## 7. Epic 10 boundary

Epic 12 reads `evt_event` through the existing `DurableEventLog` interface (or a read-only SQL
projection over the same table). It **never** writes events, publishes to the bus, adds a
producer, changes `Event.seq`, changes `metric.turn` semantics, reads `MetricTurnBuffer`, creates
billing events, or modifies an `events/**`, `observability/**` or `api/metering/**` file. New
`dp_*` migrations start at `0037`, above the Epic 10 chain head. The Epic 10 certification digest
`7e57bc14…` and freeze digest `d3d4858d…` must reproduce unchanged after every Epic 12 milestone.

## 8. Findings recorded at M12.1 (not fixed — scope)

| # | Finding | Evidence | Owner milestone |
|---|---|---|---|
| **F-1** | `agent.allow_interruptions`, `min_endpointing_delay`, `max_endpointing_delay` and `tools` are accepted by `/v1/agents` but **never copied into `StartSessionRequest`** by the frozen `create_session`. `StartSessionRequest.allow_interruptions` therefore stays `False` on the product path, so barge-in is off for every agent. `E11-7` passes because it checks only that the *runtime* reads the field. | `api/routers/sessions.py` request construction; `core/session_control.py:46` | M12.3: the console must not expose a field until it acts (AR-25). Fix by composition or ADR. |
| **F-2** | The local WebRTC app does not wire the Epic 10 spine. `build_real_session_controller` takes no `observability`, so locally no event is persisted and no `metric.turn` exists. | `api/webrtc/app.py:build_controller`, `bootstrap.py:414` | M12.5 |
| **F-3** | `transcript.ts` is always `0.0` and ids are random, so the table has no reliable order. | `_persist_transcript` in both apps | M12.5 (order from `evt_event.seq`) |
| **F-4** | The agent `language` default `en-IN` is refused by the STT plugin (`("en",)` declared). Creating an agent with defaults produces a call that drops after the greeting. | `docs/runtime/local-run.md` §6 | M12.3 (console defaults and validation) |
| **F-6** | **Three Epic 10 tests already fail on a clean checkout of `m11-local-baseline`**: `test_epic10_historical_counting.py::test_the_certification_verdict_and_digest_are_unchanged` (live digest `0cf851eb…` ≠ `7e57bc14…`), `::test_the_freeze_verdict_and_digest_are_unchanged` and `test_epic10_freeze.py::test_a_later_m10_15_release_does_not_invalidate_the_freeze` (both `fa69f933…` ≠ `d3d4858d…`). The checkpoint's "6640 passed" therefore does not reproduce from the committed tree: the clean tree gives 6637 passed and 3 failed. Cause: control **E10-7** hashes `examined = len(live api/ changes since v4.0.0)`. That is 2 at the certified commit and 9 at `d5ae2c1`. It is the live-tree count ADR-0052 Amendment 1 **HC9** says must not be a digest input, and the amendment repaired E10-3/5/6 but not E10-7. M11.16.4 appeared to restore `7e57bc14` only because its `api/` files were still untracked. Every Epic 12 `api/` addition moves the value again. Verdict stays `certified`, and all 13 control outcomes are identical between baseline and M12.1. | clean `git worktree` of HEAD, `certify_epic10()`, per-control diff (DL-0237) | ✅ **Resolved at M12.1.1** ([ADR-0052 Amendment 2](../../adr/ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md), DL-0238). `examined` now reads the certified tree, and the digests reproduce `7e57bc14…`/`d3d4858d…` exactly. |
| **F-5** | `DELETE /v1/agents/{id}` hard-deletes and `session.agent_id` is a foreign key. This is harmless on SQLite (FKs off) but fails on Postgres once sessions exist. | `api/routers/agents.py` | M12.3 (console uses archive semantics) |

## 9. Risks

| ID | Risk | Mitigation |
|---|---|---|
| R-E12-1 | Two parallel data models confuse contributors | ADR-0055 + DL-0235 name the live one. Epic 12 never imports `cp_*`. |
| R-E12-2 | Additive-only pressure tempts copy-paste of frozen logic | DP2: call frozen functions. Review checks for duplication (AI rule 14). |
| R-E12-3 | Conversations/analytics show nothing until the spine is wired | F-2 is scheduled in M12.5, before any UI depends on events. |
| R-E12-4 | Frozen defects on the product path (F-1, F-5) | Composition-level fixes under their own ADRs. The UI hides fields that do not act. |
| R-E12-5 | `RendezvousRegistry` is in-process, so only one worker can run | Fine for Stage 1/local. Recorded for Stage 2. |
| R-E12-6 | Analytics mistaken for billing | No cost or consumption numbers. `metric.turn` only. Missing is never 0. |
| R-E12-7 | npm supply chain | Lockfile committed, minimal dependency set, each logged (DL-0236), `npm audit` in CI. |
| R-E12-8 | Legacy tables have no migrations (`create_all`) | `dp_*` get their own chain from `0037`. Legacy tables are not altered. |
| R-E12-9 | Cookie auth needs CSRF protection | `SameSite=Strict`, same-origin only, custom header required on mutating console calls (security model §4). |
| R-E12-10 | The UI implies capabilities that do not exist | `/v1/console/meta` reports only implemented capabilities. The UI marks the rest "not yet available". |

## 10. Compatibility & migration plan

* The developer API's paths, request/response schemas and error shapes are unchanged. SDK work
  targets them as they are.
* `api.main:app` and `api.webrtc.app:build_webrtc_app` keep working exactly as documented in
  `docs/runtime/local-run.md`. The console app is an additional entrypoint, not a replacement.
* Existing orgs created with `python -m api.bootstrap` gain console access in M12.2 through a CLI
  that attaches an owner user to an existing org. No data migration is needed.
* `dp_*` tables are additive. Dropping them returns the database to its M11 shape.
* Every milestone re-runs: full suite, ruff, `uv lock --check`, `git diff --check`, the Epic 10
  certification digest `7e57bc14…` and freeze digest `d3d4858d…` (live and historical; F-6 resolved), the frozen-file check, and the console typecheck/test/build.
