# Epic 12 — Developer Platform: Low-Level Design

| | |
|---|---|
| **Status** | Living (M12.2). §2 (M12.1) and §3 (M12.2) are **implemented**. §4–§9 are **designed**, and each is implemented and re-verified in the milestone named. |
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
│   ├── playground.py  rendezvous + session start/end from a console identity                   [M12.4]
│   ├── conversations.py  list/detail projections                                               [M12.5]
│   ├── analytics.py   metric.turn projections                                                  [M12.6]
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

## 4. Agents (M12.3)

The console reads and writes the **runtime `agent` row**. It is the same row `/v1/agents` serves,
so there is one writer path per field and no copy (§A2.5). Before M12.3 exposes a field, the
milestone verifies by test that the field **reaches `StartSessionRequest` and acts**. Fields
known at M12.1:

| Field | Reaches runtime today? | Console at M12.3 |
|---|---|---|
| name, system_prompt, first_message, llm_model, tts_voice | yes | expose |
| language | yes, but must be declared by the STT plugin (F-4) | expose, with the declared set as options |
| allow_interruptions | **no** (F-1) | hidden until fixed by composition/ADR |
| min/max_endpointing_delay, tools | **no** | hidden |
| stt_provider/tts_provider | read; only `default` is meaningful under the fixed profile | hidden |

Delete is **archive** (`dp_agent_state.archived_at`). Archived agents are hidden from lists and
refused by the Playground. This avoids F-5 on Postgres.

## 5. Playground (M12.4)

```
console (SDK core)                     api/console/playground.py            M11 (frozen, reused)
 1 POST /v1/console/playground/rendezvous ─► current_member ─► RendezvousRegistry.reserve(org.id)
   ◄─ {rendezvous_id, signalling_token, expires_in_s}
 2 POST /v1/console/playground/sessions {agent_id, rendezvous_id}   (blocks until peer usable)
                                        ─► agent owned & not archived?
                                        ─► create_webrtc_session(body, request, org, db, registry)
                                               └► create_session ─► SessionController.start
 2' concurrently: GET /v1/webrtc/rendezvous/{id}/offer, POST …/answer   (x-signalling-token)
 3 audio flows peer↔runtime; transcripts/events as M11
 4 POST /v1/console/playground/sessions/{id}/end ─► end_session(session_id, org, db, controller)
```

The browser holds only the signalling token (DP4). The SDK core ports the offer/answer loop from
`client.html` and becomes the Playground's client. The same code later ships in `sdk/`.
Microphone permission, device selection, mute and hang-up live in the SDK. Status (connecting,
connected, failed, ended) comes from the peer connection state.

## 6. Conversations (M12.5)

* **Spine wiring (F-2):** the console app composes the controller with
  `build_observability_plane(durable=SqlDurableEventSpine(EventDatabase(url)))` at the composition
  root (ADR-0050 SW2). The URL is the same System of Record. This adds no module and no producer.
* **List** `GET /v1/console/conversations?agent_id&status&cursor&limit` → `session` rows for
  `member.org.id`, newest first. Keyset cursor on `(started_at, id)`.
* **Detail** `GET /v1/console/conversations/{id}` → session, plus a transcript ordered by
  `evt_event.seq` (F-3), plus the per-turn `metric.turn` payloads, plus a lifecycle event timeline
  (allow-listed types only: `session.*`, `turn.*`, `transcript.completed`, `interrupt.confirmed`,
  `provider.*`, `runtime.error`). Raw internal payloads are not passed through. Each item is mapped
  to a product view.
* **Tenancy**: the session is fetched by `(id, org_id)`. Events are read by `session_id` **and**
  filtered on `org_id`, so defence in depth holds even for a malformed row.
* Transcript text is shown to members of the owning org only. Retention/redaction policy is
  inherited from §A11.2 and is out of scope for Epic 12.

## 7. Analytics (M12.6)

A pure projection over `metric.turn` in `evt_event`, grouped per org, agent and time bucket:
sessions, turns, session duration (from `session.started_at/ended_at`), and voice-to-voice
p50/p95 (ADR-0038 / ADR-0053 semantics, `voice_to_voice_ms`). **A missing boundary is excluded
from the percentile and counted as `missing`, never 0** (`E11-10`). No cost, no provider usage and
no billing figures (R-E12-6). If query cost becomes a problem, a `dp_turn_metric` read model can be
derived later with the event log remaining the source of truth. That is not planned now.

## 8. API keys (M12.7)

* **Create** uses the frozen `generate_api_key` + `hash_key`. It inserts `apikey`
  (org-scoped) and `dp_api_key_meta` (creator, `display_prefix` = first 8 chars, label). The raw
  key is returned **once**.
* **List** joins `apikey` ⟕ `dp_api_key_meta`: label, prefix, created, created_by, last_used_at.
  It never returns a hash.
* **Revoke** deletes the `apikey` row, so the frozen `get_org` rejects the key on the next request.
  It records `revoked_at`/`revoked_by` in `dp_api_key_meta`, which survives as the audit record.
* Provider credentials are **not** API keys and never appear here (security model §5).

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
