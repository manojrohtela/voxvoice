# Epic 12 — Developer Platform: Low-Level Design

| | |
|---|---|
| **Status** | Living (M12.1). §2 is **implemented**. §3–§9 are **designed**, and each is implemented and re-verified in the milestone named. |
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
│   ├── auth.py        signup · login · logout · me                                             [M12.2]
│   ├── orgs.py        current org · members                                                    [M12.2]
│   ├── agents.py      console agent management over the runtime `agent` row                    [M12.3]
│   ├── playground.py  rendezvous + session start/end from a console identity                   [M12.4]
│   ├── conversations.py  list/detail projections                                               [M12.5]
│   ├── analytics.py   metric.turn projections                                                  [M12.6]
│   └── api_keys.py    list · create (one-time reveal) · revoke                                 [M12.7]
├── identity/          users, password hashing, console sessions, membership, dependencies     [M12.2]
└── persistence/       dp_* SQLModel tables + migration chain from 0037                         [M12.2]
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
4. `register_console_error_handler(app)` registers a handler for **`ConsoleError` only**. It does
   not register for `HTTPException`/`RequestValidationError`, so M11 routes keep their
   `{"detail": …}` bodies.
5. `app.include_router(meta.router)`.
6. If `settings.dist_dir` is set: mount the SPA at `/console` (assets + index fallback).

### 2.2 `GET /v1/console/meta`

Unauthenticated, read-only, and free of secrets. It returns what the running process *is* and
what the console can do **now**:

```json
{
  "api_version": "v1",
  "console_api_revision": "m12.1",
  "organization_term": "organization",
  "runtime": {"transport": "local", "webrtc_signalling": false,
              "ice_policy": "no ICE servers (explicit; host candidates only)"},
  "capabilities": ["runtime.status"],
  "console_bundle": false
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

## 3. Identity (M12.2)

* **Signup** `POST /v1/console/auth/signup {email, password, display_name, organization_name}` →
  in one transaction: `dp_user` (scrypt hash) + `org` row (the frozen model, **no API key
  created**) + `dp_membership(role=owner)` + `dp_console_session`. Sets the cookie. Gated by
  `VOXVOICE_CONSOLE_SIGNUP` (`open` | `closed`).
* **Login** verifies scrypt in constant time and returns the same generic error for unknown
  email and wrong password. It rotates the session (new token every login). Rate limiting is
  per email and per client (M12.2 decides an in-process limiter, documented as Stage-1 only).
* **Session**: 32-byte random token, stored as SHA-256 in `dp_console_session`. Cookie
  `vv_console` has `HttpOnly; SameSite=Strict; Path=/`, plus `Secure` outside dev. Idle and
  absolute expiry are configured. **Logout** sets `revoked_at`.
* **Dependency** `current_member(request) -> Member(user, org, role)` validates the cookie, loads
  the session → user → membership, and checks the membership is active. Every console query
  scopes by `member.org.id`.
* **CSRF**: mutating console routes require `x-voxvoice-console: 1`. A cross-site form cannot set
  that header, and a cross-site `fetch` cannot either without a CORS preflight, which is not
  granted.
* **Existing orgs**: `python -m api.console.bootstrap_owner --org <org_id> --email … --password-stdin`
  attaches an owner to an org created by `api.bootstrap`.

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
