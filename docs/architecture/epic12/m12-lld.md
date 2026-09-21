# Epic 12 — Developer Platform: Low-Level Design

| | |
|---|---|
| **Status** | Living (M12.5). §2–§6 (M12.1–M12.5) are **implemented**. §7–§9 are **designed**, and each is implemented and re-verified in the milestone named. |
| **Date** | 2026-09-21 |
| **Related** | [HLD](m12-hld.md) · [ADR-0055](../../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) · [DB design](m12-db-design.md) · [API design](../../apis/m12-api-design.md) · [Security model](../../security/m12-security-model.md) |

## 1. Backend module layout (`platform/api/console/`)

```
api/console/
├── __init__.py        public surface: build_console_app, ConsoleSettings
├── app.py             composition: wraps build_webrtc_app, adds console routers/handler/bundle   [M12.1]
├── config.py          ConsoleSettings (VOXVOICE_CONSOLE_*), validated at startup               [M12.1]
├── errors.py          ConsoleError hierarchy + scoped envelope handler                        [M12.1]
├── bundle.py          serves the built console SPA under /console/ (optional)                  [M12.1]
├── routers/
│   ├── meta.py        GET /v1/console/meta                                                     [M12.1]
│   ├── auth.py        signup · login · logout                                                  [M12.2]
│   ├── organizations.py  me · create · select · read/rename · members                          [M12.2]
│   ├── views.py       identity response models (no secret is ever a field)                     [M12.2]
│   ├── agents.py      console agent management over the runtime `agent` row                    [M12.3]
├── agents/            agent validation + tenant-scoped operations (service.py)                [M12.3]
├── analytics/         metric.turn + session projections, per the definitions contract         [M12.6]
│   ├── playground.py  rendezvous + session start/end from a console identity                   [M12.4]
│   ├── conversations.py  list/detail projections                                               [M12.5]
│   ├── analytics.py   summary · latency · timeseries · agents (read-only)                     [M12.6]
│   └── api_keys.py    list · create (one-time reveal) · revoke                                 [M12.7]
├── identity/          users, password hashing, console sessions, membership, dependencies     [M12.2]
├── persistence/       dp_* SQLModel tables + migration chain from 0037                         [M12.2]
└── bootstrap_owner.py CLI: first console owner for an org / closed sign-up                    [M12.2]
```

Naming rules (ADR-0055 DP2): no path under `api/` contains `observability` (`E10-9`). Code
identifiers name no vendor (`E11-3`). The analytics router is `analytics.py`, never
`observability.py`.

## 2. Implemented at M12.1

### 2.1 `build_console_app(settings=None, *, webrtc_settings=None) -> FastAPI`

1. `ConsoleSettings.from_env()` → `validate()`. A misconfiguration is a **startup** error (§A2.6).
2. `app = build_webrtc_app(webrtc_settings)`. This is the M11 application unchanged: lifespan,
   controller, rendezvous registry, `/health`, `/v1/agents`, `/v1/sessions`, and `/v1/webrtc/*`
   when signalling is enabled.
3. `app.state.console_settings`, `app.state.console_runtime` (the transport/signalling/ICE
   description of the wrapped app) are recorded for the meta route.
4. `register_console_error_handler(app)` registers a handler for **`ConsoleError`**, and (M12.2) a
   `RequestValidationError` handler that uses the envelope **only for `/v1/console/*` paths** and
   delegates everything else to FastAPI's default. M11 routes keep their `{"detail": …}` bodies.
5. `app.include_router(meta.router)`.
6. If `settings.dist_dir` is set: mount the SPA at `/console` (assets + index fallback).

### 2.2 `GET /v1/console/meta`

Unauthenticated, read-only, and free of secrets. It returns what the running process *is* and
what the console can do **now**:

```json
{
  "api_version": "v1",
  "console_api_revision": "m12.2",
  "runtime": {"transport": "local", "webrtc_signalling": false,
              "ice_policy": "no ICE servers (explicit; host candidates only)"},
  "capabilities": ["runtime.status", "identity"],
  "console_bundle": false,
  "signup": {"mode": "first_run", "available": true}
}
```

`capabilities` lists **only implemented** capabilities. Each milestone appends its own, for
example `identity` (M12.2), `agents` (M12.3) and `playground` (M12.4). The UI treats an absent
capability as "not yet available". It never renders a feature the server does not declare
(R-E12-10). The response carries no provider name, key, voice id or credential. Tests assert
this with sentinel credential values planted in the environment.

### 2.3 Console bundle serving (`bundle.py`)

* `GET /console` → redirects to `/console/`.
* `GET /console/{path}` → the file under `dist_dir` if it exists and resolves **inside**
  `dist_dir` (path traversal refused). Otherwise `index.html` (client-side routing).
* `dist_dir` set but missing or without `index.html` → `ConsoleConfigError` at startup.

### 2.4 Error model

```python
class ConsoleError(Exception):          # type, status, message
    InvalidRequest(400) · Unauthenticated(401) · Forbidden(403) · NotFound(404)
    Conflict(409) · RateLimited(429) · RuntimeUnavailable(503)
```

Response body: `{"error": {"type": "<snake_case>", "message": "<safe text>", "request_id": "<id>"}}`.
`request_id` is the inbound `x-request-id` when it is a bounded token (`[A-Za-z0-9._-]{1,64}`),
otherwise a fresh id. It is echoed in the `x-request-id` response header. Messages never contain
exception text from lower layers. Cross-tenant reads raise `NotFound`, never `Forbidden`, so
existence does not leak (M11 precedent).

## 3. Identity (M12.2) — implemented

Code: `api/console/identity/` (`passwords`, `throttle`, `service`, `deps`),
`api/console/persistence/` (`dp_*` models, migration `0037`), `api/console/routers/`
(`auth`, `organizations`, `views`), `api/console/bootstrap_owner.py`. Tests:
`tests/test_console_identity.py`.

* **Sign-up** `POST /v1/console/auth/signup {email, password, display_name?, organization_name}`
  → in one transaction: `dp_user` (scrypt hash) + an M11 `org` row (**no API key**) +
  `dp_membership(owner)` + `dp_console_session`. Sets the cookie. The `VOXVOICE_CONSOLE_SIGNUP`
  policy is `first_run` (default: allowed only while no user exists, then closed), `open` or
  `closed`. `/v1/console/meta` reports `signup.mode` and `signup.available`.
* **Sign-in** `POST /v1/console/auth/login {email, password}`. scrypt is verified in constant time.
  Unknown email, wrong password and disabled account return the same 401, and a dummy hash is
  verified so the timing matches. Every sign-in creates a new session token. **There is no
  endpoint that turns an identifier into a session**; the Epic 8 `/v1/auth/token` behaviour is
  not reproduced, and that app is not served by the console.
* **Throttle**: in-process, per `(email, client)`, sliding window. Once the limit is reached,
  even the right password gets 429. Stage 1 only (a shared store is needed for several API
  processes).
* **Session**: 32-byte `token_urlsafe`, stored as SHA-256. Cookie `vv_console`: `HttpOnly;
  SameSite=Strict; Path=/; Max-Age=<absolute>`, plus `Secure` unless `APP_ENV=dev`
  (`VOXVOICE_CONSOLE_COOKIE_SECURE` overrides). Absolute expiry is `expires_at`; idle expiry is
  `last_seen_at`, refreshed on each authenticated request. **Sign-out** sets `revoked_at`, so a
  copied cookie is dead too. Expired and revoked rows are purged at startup.
* **Dependencies** (`identity/deps.py`): `current_identity` returns user + session or 401.
  `current_member` returns user + active org + role, or 409 if the session has no organization or
  the membership was removed. `require_role(minimum)` returns 403 below the role. The active org
  lives **only** on the server-side session; no endpoint takes an org id as a read scope.
* **Organizations**: `GET /v1/console/me`, `POST /v1/console/organizations` (caller becomes
  owner; the session switches to it), `POST /v1/console/session/organization` (switch; 404 if not
  a member), `GET/PATCH /v1/console/organization` (rename: owner only),
  `GET /v1/console/organization/members`.
* **CSRF**: every non-GET console route requires `x-voxvoice-console: 1` (403 otherwise).
  Request-validation failures on `/v1/console/*` use the console envelope (422
  `invalid_request`). Other paths keep FastAPI's default body.
* **Startup**: the console app wraps the M11 lifespan. After M11's `init_db`, it applies the
  `dp_*` chain (`0037`) and purges dead sessions. Nothing creates a user at startup.
* **Existing orgs / closed sign-up**: `python -m api.console.bootstrap_owner --email … (--org
  <org_id> | --new-org <name>) [--password-stdin]`. The password comes from a prompt or stdin,
  never argv. An existing user is attached without a password.

**Not in M12.2:** invitations and member management (owner/admin can see members, but adding
and removing members is a later milestone), password change/reset (no email delivery locally),
account deletion, SSO/OAuth.

Role matrix (enforced server-side):

| Action | owner | admin | developer | viewer |
|---|---|---|---|---|
| read agents, conversations, analytics | ✓ | ✓ | ✓ | ✓ |
| create/edit/archive agents, use Playground | ✓ | ✓ | ✓ | — |
| create/revoke API keys | ✓ | ✓ | — | — |
| manage members | ✓ | ✓ | — | — |
| rename/delete organization | ✓ | — | — | — |

## 4. Agents (M12.3) — implemented

Code: `api/console/agents/service.py`, `api/console/routers/agents.py`, `dp_agent_state`
(migration `0038`), `bootstrap.declared_stt_languages`. Tests: `tests/test_console_agents.py`.

The console reads and writes the **runtime `agent` row**, the same row `/v1/agents` serves and
`create_session` starts from. There is one writer path per field and no copy (§A2.5). A test
drives the frozen `create_session` with a recording `SessionController` and proves that every
editable field arrives in `StartSessionRequest`. The orchestrator uses each one
(`orchestrator.py`: model, language, voice).

| Field | Reaches runtime? | Console |
|---|---|---|
| name, system_prompt, first_message, llm_model, tts_voice | yes | editable |
| language | yes; must be declared by the STT plugin (F-4) | select from `GET /v1/console/agents/options` |
| allow_interruptions | **no** (F-1) | not shown, not written |
| min/max_endpointing_delay, tools | **no** | not shown, not written |
| stt_provider/tts_provider | only `default` is meaningful | not shown, not written |

Values set on hidden fields through the developer API survive every console edit (tested).

* **Routes**: `GET /v1/console/agents` (viewer; `{data: [...]}`, newest first, excluding
  archived), `GET /v1/console/agents/options` (viewer), `POST` (developer), `GET /{id}`
  (viewer), `PATCH /{id}` (developer; only the fields sent; `null` clears an optional field),
  `POST /{id}/archive` (developer, 204). Cross-org and archived agents return 404, and every
  write needs the CSRF header.
* **Languages (F-4)**: `bootstrap.declared_stt_languages(profile)` reads the STT plugin's own
  declaration (the local class capabilities; the Deepgram plugin's constructor default, which is
  what `build_registry` constructs). No provider is built and no credential is read. The console
  app asks it for the profile `build_controller` selects (real when WebRTC is enabled, local
  otherwise). New agents default to the first declared language. An undeclared language is refused
  (400). An existing agent with one (e.g. the developer API's `en-IN` default) is shown with
  `language_supported: false` and a warning.
* **Validation**: name 1–100 characters, instructions 1–20,000, greeting ≤ 2,000 (blank →
  null). Model and voice are opaque identifiers `[A-Za-z0-9][A-Za-z0-9._:/-]*`, ≤ 200 characters.
* **Archive, not delete (F-5)**: `dp_agent_state.archived_at`/`archived_by`. The row, its
  sessions and its history remain. Archived agents disappear from the console (and, from M12.4,
  from the Playground). The developer API still serves them, and its frozen `DELETE` still
  hard-deletes. `dp_agent_state.agent_id` is `ON DELETE CASCADE`, so it never blocks that delete.
* **Not in M12.3:** unarchive, agent duplication, versioning, tools, and barge-in (F-1, below).

**F-1 is deliberately still open.** Making `allow_interruptions` act requires carrying it into
`StartSessionRequest` on the product path. The frozen `create_session` cannot do that, so a
composition-level controller decorator would have to fill it from the agent row. That would switch
barge-in **on** for every existing agent (the model default is `True`) on a runtime path that
cannot be verified without a live voice session. The ADR-0046 interrupt behaviour on speakers
(self-interruption, `local-run.md` §8) is exactly the risk. It needs an owner decision and a live
check, so the console hides the field instead of pretending it works (AR-25). `audio_endpointing`
is not carried either (F-7).

## 5. Playground (M12.4) — implemented

Code: `api/console/routers/playground.py`, `console/src/voice/call.ts` (`VoiceCall`),
`console/src/features/playground/`. Tests: `tests/test_console_playground.py` (Python, including
a real WebRTC call), `console/src/voice/call.test.ts`, and `Playground.test.tsx`.

```
browser (VoiceCall)                   api/console/routers/playground.py      M11 (frozen, reused)
 1 getUserMedia (on a click)
 2 POST /v1/console/playground/rendezvous ─► developer role ─► RendezvousRegistry.reserve(org.id)
   ◄─ {rendezvous_id, signalling_token, expires_in_s}
 3 POST /v1/console/playground/sessions {agent_id, rendezvous_id}      (blocks until peer usable)
                                         ─► agent in org, not archived, language declared?
                                         ─► create_webrtc_session(…, metadata={source, started_by})
                                                └► create_session ─► SessionController.start
 3' concurrently: GET  /v1/webrtc/rendezvous/{id}/offer   (x-signalling-token; 204 = not yet)
                  POST /v1/webrtc/rendezvous/{id}/answer  (x-signalling-token)
 4 real audio both ways; the agent's voice plays in an <audio> element
 5 POST /v1/console/playground/sessions/{id}/end ─► end_session(…) ─► rendezvous released
```

* **One runtime.** Every route calls a frozen M11 function. There is no second signalling
  channel, WebRTC implementation, session API or worker. The browser's offer/answer uses the
  existing `/v1/webrtc/rendezvous/*` endpoints with the single-purpose token.
* **Authorization (server-side, before the runtime is touched):** signed-in, `developer` role or
  higher in the session's organization, and the CSRF header. The agent must belong to that
  organization (otherwise 404), must not be archived (404), and must use a language the STT plugin
  declares (400 with the reason). A foreign rendezvous is refused by the frozen `check_bindable`
  (404). Ending a foreign session returns 404. The frozen handlers' `HTTPException`s are
  re-expressed in the console envelope (`from_http_exception`); a runtime start failure is
  `runtime_failed` (502).
* **Voice not enabled:** without the signalling surface the routes answer 503
  `runtime_unavailable` with the fix. The UI enables calling only when meta reports
  `transport: webrtc` **and** signalling, and otherwise explains how to enable it.
* **Recorded:** the session row's metadata is `{"source": "console_playground", "started_by": <user_id>}`,
  for Conversations (M12.5).
* **`VoiceCall`** (framework-free, dependency-injected, the future `sdk/` client core): state
  machine `idle → requesting_microphone → reserving → connecting → connected → ending → ended |
  failed`. A refused start fails at once instead of waiting out the 30 s offer deadline. Hanging
  up while connecting ends a session that starts late, so nothing leaks. Peer `failed`/`closed`
  reports "connection lost" and ends the session. Mute disables the microphone track. The session
  is ended on `pagehide` (`fetch keepalive`, same-origin, with the CSRF header).
* **Not in M12.4:** live transcripts and latency in the Playground (Conversations, M12.5; they need
  the event read path), barge-in (F-1), device selection.

## 6. Conversations (M12.5) — implemented

Code: `api/console/conversations/service.py`, `api/console/routers/conversations.py`,
`api/console/session_defaults.py`, the composition in `api/console/app.py`; M11 additions in
`api/webrtc/app.py` (the observability passthrough and the transcript `ts`) and
`bootstrap.build_real_session_controller` (an `observability` passthrough). Tests:
`tests/test_console_conversations.py` and `console/src/features/conversations/`.

* **Event log wired (F-2, ADR-0055 DP10):** the console app attaches `SqlDurableEventLog` over the
  same `DATABASE_URL` through `build_observability_plane(durable=…, telemetry=True)`, and
  `build_webrtc_app(observability=…)` passes it to either profile's controller. The `evt_*` tables
  are created before the runtime starts. There is no outbox, no metric buffer, and no new producer
  or event type.
* **Audio endpointing (F-7, DP11):** `AudioEndpointingController` sets
  `StartSessionRequest.audio_endpointing=True` on the real profile only (switchable). Evidence: the
  same M11 conversation gives two metrics with no `voice_to_voice_ms` without it, and one complete
  metric with it.
* **Transcript order (F-3):** the WebRTC app's sink writes `ts` = seconds since `session.started_at`.
  The read orders by `(ts, id)`. `transcript_ordered: false` when a session has ≥ 2 rows that are
  all `0.0` (the frozen default app, and pre-M12.5 rows); their order is never guessed.
* **List** `GET /v1/console/conversations?agent_id&status&cursor&limit`: `session` rows of the
  session's organization, newest first, keyset cursor on `(started_at, id)`, `limit` ≤ 100.
  Each row has the agent (name, or null if deleted), status, channel, `source`
  (`console_playground` from session metadata, else `api`), times, `duration_s` (only when ended)
  and `message_count` (final transcript rows).
* **Detail** `GET /v1/console/conversations/{id}`: the summary, plus the transcript, plus
  `turns` (from `metric.turn`: `voice_to_voice_ms`, `stages_ms` for endpoint/stt/llm/tts/egress,
  `user_speech_ms`, and `unmeasured` stages; absent is never 0), plus `timeline` (allow-listed
  lifecycle types with labels and offsets; engine detail such as token streams and routine
  provider connect/disconnect is excluded), plus `events_recorded`. The raw payload and the
  monotonic `at` instants are never returned.
* **Tenancy:** the session is fetched by id and checked against the member's org (404 otherwise).
  Events are read with **both** `session_id` and `org_id` predicates, using the same query shape as
  `SqlDurableEventLog.read_session` and Epic 10's own `to_event` mapping, on the request's session.
* **Linkage:** the Playground shows "View this conversation" after a call, and the agent editor
  links to its conversations.
* **Not in M12.5:** live (in-call) transcripts, recordings, search, retention/redaction policy,
  and aggregate analytics (M12.6).

## 7. Analytics (M12.6) — implemented

Code: `api/console/analytics/service.py`, `api/console/routers/analytics.py`. Contract:
[`m12-analytics-definitions.md`](m12-analytics-definitions.md). Tests:
`tests/test_console_analytics.py` and `console/src/features/analytics/`.

* A pure projection over `session` (the conversations), `agent`/`dp_agent_state`, and `metric.turn`
  + `session.started` in `evt_event`: sessions, turns, session duration (from
  `session.started_at/ended_at`), voice-to-voice and stage latency with `samples`, `missing` and
  nearest-rank p50/p95 (ADR-0038 / ADR-0053 semantics, `voice_to_voice_ms`), per-day and per-agent
  usage, and lifecycle and turn outcomes. **A missing boundary is excluded from every statistic and
  counted as `missing`, never 0** (`E11-10`). No cost, no provider usage and no billing figures
  (R-E12-6). No end reason (not recorded) and no barge-in rate (F-1).
* `GET /v1/console/analytics/{summary, latency, timeseries, agents}`, `viewer`+. Shared parameters
  `range=today|7d|30d` or `from/to` (UTC days, inclusive, ≤ 92), `agent_id` (404 when not the
  organization's), `source=playground|api`. The resolved query is echoed on each response.
  `analytics_today()` is the single clock (UTC), and a dependency so tests can pin it.
* Reads: the conversation population, the turn population via `IN` (numeric fields extracted in
  SQL), and the recorded marks. Arithmetic is done in Python because durations and percentiles are
  not portable SQL across SQLite and Postgres. Measured at 45–140 ms for 5,000 conversations / 845,000
  events on SQLite. The frozen-schema index finding is in the definitions document §6.
* If query cost becomes a problem, a `dp_turn_metric` read model can be derived later, with the
  event log remaining the source of truth. That is not planned now.

## 8. API keys and the developer contract (M12.7) — implemented

Code: `api/console/api_keys/service.py`, `api/console/routers/api_keys.py`,
`api/console/openapi.py`. Tests: `tests/test_console_api_keys.py` and
`console/src/features/developers/`. Contract: [`docs/apis/developer-api.md`](../../apis/developer-api.md).

* **Create** `POST /v1/console/api-keys {label}` (admin+, CSRF) uses the frozen
  `generate_api_key` + `hash_key` and inserts one `apikey` row. The raw key is returned **once**,
  with `Cache-Control: no-store`.
* **List** `GET /v1/console/api-keys` (admin+): `{data: [{id, label, created_at, last_used_at}]}`,
  newest first. It never returns the hash or any part of the key.
* **Revoke** `POST /v1/console/api-keys/{id}/revoke` (admin+, CSRF) → 204. It deletes the
  `apikey` row, so the frozen `get_org` rejects the key on the next request. Another org's or a
  missing key → 404.
* **Not built (ADR-0055 Amendment 3):** `dp_api_key_meta`. No creator, display prefix or
  post-revocation record is stored. Creation and revocation are logged instead (logger
  `voxvoice.console.api_keys`, INFO: key id, org, user; never key material).
* **OpenAPI** (`api/console/openapi.py`, installed on the console app): security schemes
  `ApiKeyAuth` (`x-api-key`), `SignallingToken` (`x-signalling-token`) and `ConsoleSession`
  (`vv_console` cookie). Every operation gets `security` and `x-voxvoice-surface`
  (`developer · signalling · console · public`), and credential headers are no longer duplicated as
  parameters.
* **Console:** `/developers` (API keys; owner and admin manage, others see the reason and the docs
  link) and `/developers/docs` (guide + reference generated from `/openapi.json`, for every role).
  The one-time key lives only in component state until "Done".
* Provider credentials are **not** API keys and never appear here (security model §5).

## 8a. Overview (M12.8) — implemented

Code: `api/console/routers/overview.py` (composition only), the shared view builders in
`api/console/routers/analytics.py` (`summary_figures`, `bucket_views`, `latency_views`,
`agent_usage_views`) and `routers/conversations.py` (`summary_view`), and
`agents.service.inventory`. Tests: `tests/test_console_overview.py` and
`console/src/features/overview/`.

* **One read-only endpoint**, `GET /v1/console/overview` (viewer+, console session only). It
  takes the analytics `Scope`: `range=today|7d|30d` or `from/to`, and `agent_id`. The same UTC
  semantics, validation, org resolution and foreign-agent 404 apply. `source` is refused (400),
  because the recent-conversations panel could not honour it.
* **Why an endpoint.** The same page composed in the browser takes six requests (summary,
  timeseries, latency, agents, conversations, agent inventory) and four separate analytics
  snapshots, which can describe different moments. The endpoint makes **one** snapshot and passes
  it to the functions `/analytics/*` use. No figure is computed here: a test asserts equality with
  Analytics for several ranges and agent filters.
* **Response:** `query`; `agents {active, archived, unsupported_language}` (the organization's
  inventory **now**, not affected by the range or the agent filter; "unsupported" = the M12.3
  `language_supported: false` rule); `conversations`, `duration`, `turns` (= `/analytics/summary`);
  `usage` (= `/analytics/timeseries`); `latency` (= `/analytics/latency`); `agent_activity` (first
  5 of `/analytics/agents`) and `agent_activity_more`; `recent_conversations` (the M12.5 list's
  first page with `limit=5` and the agent filter, **any date**, labelled as such); and `attention`,
  deterministic conditions only: `no_agents`, `unsupported_language`, `no_conversations` (in
  range, when agents exist). There are no thresholds, scores, "health", cost or generated text.
* **Queries:** one analytics snapshot (≤ 3 reads, analytics definitions §6), the agents-analytics
  name and archive reads, 2 inventory reads, and 1 conversation page. None is per-agent or
  per-conversation.
* **Console:** the Overview page keeps the runtime status and Get started cards. It adds range
  and agent filters and the composed panels (summary, attention, usage, latency snapshot with
  median and sample counts, recent conversations, agent activity), all from one request. It skips
  the dashboard on a server that does not declare the `overview` capability.
* **Organization switching** (a defect found here, affecting every view): the shell keyed each
  view only by path, so switching organization left the previous organization's data on screen.
  It is now keyed by organization and path, and the switcher drops URL filters, which belong to the
  previous organization.

## 9. SDK (M12.4 core → M12.9 release)

* **`@voxvoice/client`** (TypeScript, browser): `connect({rendezvousId, signallingToken, baseUrl})`
  → a `Call` with `state`, `on('state'|'error'|'ended')`, `mute()`, `hangUp()`. It never sees an
  API key.
* **`@voxvoice/server`** (TypeScript, Node) and a **Python helper**: wrap
  `POST /v1/webrtc/rendezvous` + `POST /v1/webrtc/sessions` with the API key, for a developer's
  backend. It hands the browser only `{rendezvousId, signallingToken}`.
* **`@voxvoice/react`**: a `useVoxVoiceCall()` hook over the client.
* Both SDKs target the **developer API v1 exactly as it exists**. The SDK is released only after
  the API contract is frozen for v1 (M12.9).

## 10. Versioning

* Developer API: `/v1`, additive only. A breaking change requires `/v2` plus a deprecation window
  (§A11.4).
* Console API: `/v1/console`. `console_api_revision` (for example `m12.1`) lets the console detect
  a mismatched backend. It is not a public contract.
* SDK packages: SemVer, independent of the platform version (GIT_WORKFLOW §4).
* `dp_*` schema: forward-only migrations numbered from `0037`.
