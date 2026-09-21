# Epic 12 — API Design

| | |
|---|---|
| **Status** | Living (M12.1). The "M12.1" rows are **implemented**. Other rows are **designed** and land in the milestone named. |
| **Date** | 2026-09-21 |
| **Related** | [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP4/DP7 · [LLD](../architecture/epic12/m12-lld.md) · [Security model](../security/m12-security-model.md) |

## 1. Two surfaces, one server

| | Developer API | Console API |
|---|---|---|
| Audience | a developer's **backend** and the SDK | the first-party web console |
| Prefix | `/v1/agents`, `/v1/sessions`, `/v1/webrtc/*` | `/v1/console/*` |
| Auth | `x-api-key` (hashed org key). Signalling routes use `x-signalling-token`. | `vv_console` session cookie + `x-voxvoice-console: 1` on mutations |
| Stability | public; additive-only within `/v1` | first-party; versioned, may evolve per milestone |
| Error body | `{"detail": …}` (FastAPI; unchanged, frozen) | `{"error": {"type","message","request_id"}}` |
| Status | exists (M11), **unchanged** by Epic 12 | new (Epic 12) |

The existing M11 session path is authoritative. The console API **never** defines a second
session-creation contract. It resolves a console identity to an org and calls the frozen handler
(DP5).

## 2. Developer API v1 (existing, unchanged)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST/GET | `/v1/agents` | key | create / list agents |
| GET/PATCH/DELETE | `/v1/agents/{id}` | key | read / update / delete |
| POST | `/v1/sessions` | key | start a session (loopback / non-WebRTC channels) |
| POST | `/v1/sessions/{id}/end` | key | end |
| GET | `/v1/sessions/{id}`, `/v1/sessions/{id}/transcript` | key | read |
| POST | `/v1/webrtc/rendezvous` | key | reserve a signalling slot → `{rendezvous_id, signalling_token, expires_in_s}` |
| GET | `/v1/webrtc/rendezvous/{id}/offer` | token | long-poll (204 = not yet) |
| POST | `/v1/webrtc/rendezvous/{id}/answer` | token | deliver the answer |
| POST | `/v1/webrtc/sessions` | key | start a session on a reserved rendezvous (blocks until the peer is usable) |
| GET | `/health` | none | liveness + transport description |

## 3. Console API

| Method | Path | Min role | Milestone | Notes |
|---|---|---|---|---|
| GET | `/v1/console/meta` | none | **M12.1** | version, runtime description, implemented capabilities. No secrets. |
| POST | `/v1/console/auth/signup` | none | M12.2 | gated by `VOXVOICE_CONSOLE_SIGNUP` |
| POST | `/v1/console/auth/login` | none | M12.2 | sets the cookie |
| POST | `/v1/console/auth/logout` | any | M12.2 | revokes the session |
| GET | `/v1/console/me` | any | M12.2 | user, memberships, active org, role |
| GET/PATCH | `/v1/console/organization` | viewer/owner | M12.2 | current org |
| GET | `/v1/console/organization/members` | viewer | M12.2 | |
| GET/POST | `/v1/console/agents` | viewer/developer | M12.3 | runtime `agent` rows for the org, excluding archived |
| GET/PATCH | `/v1/console/agents/{id}` | viewer/developer | M12.3 | only fields verified to act (LLD §4) |
| POST | `/v1/console/agents/{id}/archive` | developer | M12.3 | archive, not delete |
| GET | `/v1/console/agents/options` | viewer | M12.3 | declared languages, available models/voices where the runtime can state them |
| POST | `/v1/console/playground/rendezvous` | developer | M12.4 | → `{rendezvous_id, signalling_token, expires_in_s}` |
| POST | `/v1/console/playground/sessions` | developer | M12.4 | `{agent_id, rendezvous_id}` → frozen `create_webrtc_session` |
| POST | `/v1/console/playground/sessions/{id}/end` | developer | M12.4 | → frozen `end_session` |
| GET | `/v1/console/conversations` | viewer | M12.5 | keyset pagination |
| GET | `/v1/console/conversations/{id}` | viewer | M12.5 | session + ordered transcript + turn metrics + lifecycle timeline |
| GET | `/v1/console/analytics/summary` | viewer | M12.6 | `?from&to&agent_id` |
| GET | `/v1/console/analytics/latency` | viewer | M12.6 | voice-to-voice p50/p95 + `missing` count |
| GET/POST | `/v1/console/api-keys` | admin | M12.7 | list / create (raw key returned once) |
| POST | `/v1/console/api-keys/{id}/revoke` | admin | M12.7 | |

Collection responses use `{"data": [...], "next_cursor": "…" | null}`. Single resources return
the object directly.

## 4. Error model

Console API errors:

```json
HTTP/1.1 404 Not Found
x-request-id: 3f1c…

{"error": {"type": "not_found", "message": "Agent not found", "request_id": "3f1c…"}}
```

| `type` | HTTP | When |
|---|---|---|
| `invalid_request` | 400 / 422 | malformed input, failed validation |
| `unauthenticated` | 401 | no or expired session |
| `forbidden` | 403 | authenticated, but the role is insufficient *within the user's own org* |
| `not_found` | 404 | missing **or belongs to another org** (existence never leaks) |
| `conflict` | 409 | e.g. email already registered, rendezvous already bound |
| `rate_limited` | 429 | login throttling |
| `runtime_unavailable` | 503 | runtime/signalling not configured |
| `internal` | 500 | anything unexpected. The message is generic. Detail goes to logs only. |

Messages are written for the user and never include stack traces, SQL or provider responses.

## 5. Versioning & compatibility

Developer API rules follow §A11.4 (additive within `/v1`). `console_api_revision` in
`/v1/console/meta` lets the console refuse to run against an incompatible backend. The SDK pins a
developer-API major version.
