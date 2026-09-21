# ADR-0055 — The Developer Platform (Epic 12) productizes the M11 runtime model through additive modules

| | |
|---|---|
| **Status** | **ACCEPTED 2026-09-21** (M12.1). DP1 and DP8 were put to the Technical Owner as explicit choices at the M12.1 gate and ratified there; the rest follow from constraints already in force. |
| **Kind** | Product-architecture ADR. **Amends no Part A section.** Adds one row to the §A14 responsibility map (`console/`, DP8), which §A12 allows as an additive extension point (a new Consumer). |
| **Deciders** | Technical Owner |
| **Amended** | Amendment 1 (M12.5): DP10 durable log without outbox, DP11 audio endpointing on the real path |
| **Related** | [ADR-0003](ADR-0003-two-plane-architecture.md), [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) (Studio is a Consumer), [ADR-0043](ADR-0043-m10.13-certification.md) CD7 (`E10-7`), [ADR-0049](ADR-0049-provider-credential-injection.md), [ADR-0050](ADR-0050-epic10-spine-telemetry-metering-production-wiring.md), [ADR-0051](ADR-0051-epic11-certification-control-set.md), [ADR-0052](ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md), DL-0226, DL-0235, DL-0236, DL-0237; [Epic 12 HLD](../architecture/epic12/m12-hld.md) |

## Problem

M11 proved VoxVoice can hold a real spoken conversation. It did so through an operator path: a
CLI creates an org and an API key, `curl` creates an agent, and a reference HTML page takes the
**org API key** pasted into a text box. There is no login, no user, no dashboard, no conversation
history view, no analytics, and no SDK. Epic 12 turns the runtime into a product a developer can
use. Before any product code lands, four questions need an answer, because every later
milestone builds on them:

1. Which data model is the product's tenant and agent spine?
2. Where does product code live, given the freezes?
3. How does the product reach the real runtime without a second runtime, a second WebRTC path or
   a second session API?
4. Where does the web console live, and how is it built?

## Context

**The repository has two platforms that never meet.** Established by inspection at M12.1
(see HLD §2):

| | M11 runtime model | Epic 8 customer platform |
|---|---|---|
| Tables | `org`, `apikey`, `agent`, `session`, `transcript` | `cp_user`, `cp_organization`, `cp_workspace`, `cp_project`, `cp_agent` + versions, `cp_conversation_*`, RBAC, audit, … |
| Auth | hashed org API key (`x-api-key`) | HS256 JWT; `/v1/auth/token` mints a token for **any** user id. There is no credential store. |
| Runtime reached | **the real M11 path**: `create_session` → `SessionController` → `RuntimeSessionService` | **none**: deterministic mocks (`runtime_mocks.py`) and an offline `EchoTransport` |
| Tenant on events | `evt_event.org_id` is the M11 `org.id` | a disjoint id space |

**Everything under `platform/api/` that existed at `v4.0.0` is frozen.** `E10-7` (ADR-0043 CD7)
fails if any such file is modified or deleted. Additions are permitted. That covers all of Epic 8
(`api/customer`, `api/foundation`, `api/persistence`, `api/application`, `api/adapters`) and M11's
own Control Plane (`api/main.py`, `api/models.py`, `api/security.py`, `api/routers/*`). DL-0226
records M11.16.4 tripping this control and rebuilding as additions. `E10-9` also fails on any
file under `platform/api/` whose path matches `*observability*`, including **new** ones. `E11-3`
forbids vendor identifiers in `api/**`, and `E11-2` forbids `runtime`/`providers` importing `api`.

**The Constitution already fixes parts of the answer.** §A11.2 requires org-scoped hashed API
credentials for consumers and short-lived, session-scoped credentials for callers. Part B fixes the
client SDK as a TypeScript core with a React binding. ADR-0021 makes any management surface a
**Consumer** that owns no runtime state and treats Sessions and Analytics as **event-log
projections**.

## Decision

### DP1 — The M11 runtime model is the product spine *(owner ruling, M12.1 gate)*

The tenant is the M11 **`org`**. The agent is the M11 **`agent`** row, the one
`api/routers/sessions.py` reads and hands to the runtime. A session is the M11 **`session`**.
Epic 12 adds only what does not exist: users with credentials, memberships, console sessions and
API-key metadata. These are new `dp_*` tables (DB design §3).

The Epic 8 `cp_*` model is **not used, modified or mounted** by Epic 12. It stays frozen, tested
and intact. Converging the two models is a separate, later decision (DL-0235).

*Why:* only the M11 model reaches the real runtime. Using Epic 8 would need a
`cp_organization`↔`org` mapping plus a sync from versioned `cp_agent` into the runtime `agent`
row. That sync would be a second writer of agent configuration (§A2.5), and it would all have to
be built before the first real call.

### DP2 — Additive-only under `platform/api/`; the product surface is `api/console/`

All Epic 12 backend code lives in **new** modules, starting with `platform/api/console/`. No file
that existed at `v4.0.0` is edited. Where Epic 12 needs a frozen behaviour, it **calls the frozen
function**, as `api/routers/webrtc.py` already calls `create_session`. It does not copy or
re-implement it. No Epic 12 file under `api/` may have `observability` in its path (`E10-9`). No
vendor identifier appears in Epic 12 code under `api/` (`E11-3`).

### DP3 — One local product application, composed by wrapping the M11 WebRTC application

`api.console.app:build_console_app` calls `api.webrtc.app:build_webrtc_app` and **adds** the
console routers, the console error handler and, optionally, the built console bundle. The M11
application is not copied. There is no second lifespan, controller, registry, health endpoint or
CORS policy. The developer API (`/v1/agents`, `/v1/sessions`, `/v1/webrtc/*`) is served
unchanged by the same process.

### DP4 — Three authentication planes, never mixed

| Plane | Who | Credential | Where it lives |
|---|---|---|---|
| **Console user** | a person using the dashboard | opaque server-side session token in an `HttpOnly`, `SameSite=Strict` cookie (M12.2) | browser cookie jar; only a hash is stored server-side |
| **Developer API** | a developer's backend | the existing hashed org API key (`x-api-key`) | the developer's server; **never** a browser |
| **Runtime client** | a browser/mic peer | the existing single-purpose rendezvous signalling token | the browser, for one SDP exchange |

The console never asks a user for an API key. The browser never holds an org API key or a
provider credential. Provider credentials stay behind the ADR-0049 `CredentialProvider` and are
never readable through any Epic 12 surface.

### DP5 — The Playground is the M11 session path, reached from a console identity

A console playground call authenticates the **user** and resolves their **org**. It then invokes
the frozen functions the M11 WebRTC router already composes: `RendezvousRegistry.reserve(org.id)`,
and `create_webrtc_session` (which calls `create_session`). The browser receives only the
rendezvous id and signalling token, and runs the same offer/answer exchange against the existing
`/v1/webrtc/rendezvous/*` endpoints. There is no second runtime, no second WebRTC implementation,
no second session API, and no bypass of `RuntimeSessionService`.

### DP6 — Conversations and Analytics are read-only projections

Conversations project `session`, `transcript` and the Epic 10 durable log (`evt_event`). Analytics
project `metric.turn` from `evt_event`. Epic 12 **writes no event**, adds no producer, no event
type, no bus, no outbox and no metric. It never reads `MetricTurnBuffer` (not durable, SW11). An
unreached latency boundary is an absent value and is never shown as 0 (`E11-10`). The durable spine
is attached to the console application's controller **by composition only** (ADR-0050 SW2), in the
milestone that first reads it (M12.5).

### DP7 — Two API surfaces, one version line

* **Developer API** (public, SDK-backed): the existing `/v1/agents`, `/v1/sessions`,
  `/v1/webrtc/*`, all unchanged. Additive-only within `/v1` (§A11.4). Its FastAPI
  `{"detail": …}` error shape is retained for compatibility.
* **Console API** (first-party): `/v1/console/*`. It is versioned from day one (AI_AGENT_RULES
  #28) but is not part of the public SDK stability promise. Errors use the envelope
  `{"error": {"type", "message", "request_id"}}`, the same shape Epic 8 established. The handler
  is **scoped to console exceptions**, so M11 routes' error bodies cannot change.

### DP8 — The console is a React + TypeScript + Vite Consumer at `console/` *(owner ruling, M12.1 gate)*

A new top-level `console/` directory holds the web console. It is a **Consumer** under ADR-0021,
alongside the existing `sdk/` slot. In development it is served by Vite with a same-origin proxy
to the API. In local product mode the API serves the built bundle under `/console/` when
`VOXVOICE_CONSOLE_DIST` names a directory. The SDK (`sdk/`, TypeScript core + React binding, Part
B) is extracted from the Playground's working client in M12.4 and published in M12.9, so the
console is the SDK's first consumer.

### DP9 — Organization, not "workspace"

The glossary rule (§A0: one concept, one name) and the existing Epic 8 `cp_workspace` entity make
"workspace" ambiguous. Epic 12 calls the tenant **Organization** in code, API and UI.

## Alternatives considered

| | Alternative | Why not |
|---|---|---|
| A1 | Build on the Epic 8 `cp_*` model | Needs a credential store, an id mapping and an agent sync (a second writer) before anything reaches the runtime. Offered at the gate and declined. |
| A2 | Edit `api/main.py` / `api/models.py` / `api/security.py` to add users and revocation | Violates `E10-7`, the exact near-miss recorded in DL-0226. |
| A3 | A standalone console backend app with its own lifespan and controller | Duplicates the M11 WebRTC composition and risks two controllers or two registries in one deployment. |
| A4 | Hand the browser the org API key (as the M11 reference client does) | Acceptable for an operator page and wrong for a product. §A11.2 requires session-scoped caller credentials. |
| A5 | JWT bearer in `localStorage` for console auth | Not revocable before expiry, and readable by any injected script. An opaque server-side session in an `HttpOnly` cookie is revocable and invisible to JS. |
| A6 | Extend the vanilla Epic 8 Studio file | No types and no component model. Offered at the gate and declined. |

## Consequences

* M11 and Epic 10 stay untouched *by construction*: every Epic 12 path either adds a module or
  calls a frozen one.
* Two parallel platforms remain in the tree. This is recorded (DL-0235) rather than hidden, and
  Epic 12 docs name which one is live.
* Known product-path defects in frozen code cannot be fixed in place. The first is F-1: the
  `allow_interruptions` field is not carried into `StartSessionRequest` (HLD §8). Each must be
  fixed by composition-level additions or by an ADR that authorizes a specific modification.
* `platform/api/` keeps growing by addition only. DP2's naming rules keep `E10-9` and `E11-3`
  green.

## Risks

See [HLD §9](../architecture/epic12/m12-hld.md#9-risks). The principal ones: two data models
coexist (R-E12-1), frozen defects on the product path (R-E12-4), and cookie-based auth requires
CSRF discipline (R-E12-9).

## Future impact

Each later Epic 12 milestone (plan: [epic12-implementation-plan.md](../roadmap/epic12-implementation-plan.md))
builds inside DP1–DP9. Converging with Epic 8, team invitations, SSO, a public read API for
events, telephony channels and billing are each additive, and each needs its own decision.

## Amendment 1 — the console runtime's event log and endpointing (M12.5, 2026-09-21)

**Status:** Accepted 2026-09-21 (M12.5, which the owner scoped to include F-2 and F-7). It is
**additive**: DP1–DP9 are unchanged.

**DP10 — The console composition attaches Epic 10's append-only log, not the outbox.**
`api.console.app` builds `SqlDurableEventLog(EventDatabase(DATABASE_URL))` and passes it as the
runtime's `durable` attachment through `bootstrap.build_observability_plane` (ADR-0050 **SW2**'s
seam, unchanged), with `TurnLatencyTap` as the only `metric.turn` producer (SW9). It deliberately
does **not** use `SqlDurableEventSpine`, which also writes an `evt_outbox` row per event:

* no `DeliveryTransport` is deployed here, so every outbox row would stay `PENDING` for ever;
* Conversations need history, and Epic 10 built `SqlDurableEventLog` for exactly *"a caller that
  wants history without delivery state"*.

Nothing in Epic 10 changes. Outbox, recovery and retention wiring (SW4–SW6) remain ADR-0050's, for
the milestone that deploys a delivery transport. No metric buffer is attached (SW10/SW11: nothing
pulls one here).

**DP11 — Real-voice sessions use audio-driven endpointing, by composition (finding F-7).** The
frozen `create_session` never sets `StartSessionRequest.audio_endpointing`, so the product path ran
the segment-driven profile. Measured at M12.5: each turn's `metric.turn` lacked
`voice_to_voice_ms`, and `speech_end` landed in an orphan second metric. `AudioEndpointingController`
(a `SessionController` decorator, the same seam as `RendezvousBindingController`) sets it to `True`,
changing nothing else. It is applied **only** to the real profile, which registers the
voice-activity provider audio endpointing requires. `VOXVOICE_CONSOLE_AUDIO_ENDPOINTING=false`
switches it off. **Risk:** this changes endpointing behaviour on the live path, which was not
exercised with real providers at M12.5. The live run in `local-run.md` §7a is the check.

**Also at M12.5 (F-3), recorded here because it touches M11's post-`v4.0.0` composition:** the
WebRTC app's transcript sink now writes `Transcript.ts` as seconds since the session started, as
the column documents, instead of `0.0`. The frozen default app still writes `0.0`; those sessions
are reported as unordered.

## Amendment 2 — analytics is a query layer with a written contract (M12.6, 2026-09-21)

**Status:** Accepted 2026-09-21 (M12.6). It is **additive**: DP1–DP11 are unchanged, and it makes
DP6 precise for analytics.

**DP12 — Analytics owns no data, and its definitions are a contract.**
* **Ownership.** `api/console/analytics/` computes every figure per request from the M11 `session`
  rows (the conversations, as in M12.5), `agent` and `dp_agent_state` (names, archive state), and
  `metric.turn` plus `session.started` in `evt_event`. It adds **no table, event type, producer,
  writer, buffer, index or migration**, and reads nothing from `MetricTurnBuffer`. A pre-aggregated
  read model (`dp_turn_metric`, LLD §7) remains an option only if query cost demands it, and it
  would be derived from the log, which stays the source of truth.
* **Contract.** The figures, their populations and their null semantics are defined in
  [`m12-analytics-definitions.md`](../architecture/epic12/m12-analytics-definitions.md). Changing a
  definition is a contract change for the console and the future SDK.
* **Missing is never 0.** Unmeasured latencies are excluded from statistics and counted as
  `missing`. Unfinished conversations have no duration. Conversations the log did not observe have
  unknown turns and are excluded from per-conversation averages. Statistics over nothing are
  `null`. Counts of nothing are 0.
* **Time.** UTC only: storage, query, range and buckets. Membership is by `session.started_at`,
  half-open `[start, end)`. A conversation and all its turns belong to the UTC day it started.
* **Turns are `metric.turn` records**, counted exactly as the conversation detail lists them. The
  producer's latency semantics (ADR-0038/0047/0053) are used as-is. Analytics neither redefines nor
  back-fills them (F-7: with audio endpointing off, a spoken exchange may be two records and may
  have no voice-to-voice value).
* **Not provided:** cost, tokens or provider usage (no authoritative metering exists), end reasons
  (not recorded), and barge-in rates (F-1 is unresolved).
* **Frozen boundary noted, not crossed.** The indexes that would bound the turn read on large logs
  belong to frozen schemas (M11 `session`, Epic 10 `evt_event`). None is added at M12.6.
