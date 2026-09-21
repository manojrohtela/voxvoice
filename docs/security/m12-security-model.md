# Epic 12 — Developer Platform Security Model

| | |
|---|---|
| **Status** | Living (M12.5). This is the model every Epic 12 milestone implements and tests against. §7 lists what is enforced as of M12.2. |
| **Date** | 2026-09-21 |
| **Related** | [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP4 · Constitution §A11.2 · [SECURITY_POLICY](../../.voxvoice/SECURITY_POLICY.md) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) · [API design](../apis/m12-api-design.md) |

## 1. Assets

Org data (agents, sessions, transcripts, events, metrics) · user credentials · console sessions ·
developer API keys · **provider credentials** (Deepgram/Groq/Cartesia), which are the most
sensitive because they carry spend.

## 2. Trust boundaries & credentials

| Boundary | Credential | Scope | Lifetime | Stored as |
|---|---|---|---|---|
| Person → console | `vv_console` cookie | one user, one active org | idle + absolute expiry, revocable | SHA-256 in `dp_console_session` |
| Developer backend → developer API | org API key (`x-api-key`) | one org | until revoked | SHA-256 in `apikey` |
| Browser peer → signalling | rendezvous signalling token | one SDP exchange, one org | the rendezvous TTL | in-memory registry |
| Runtime → providers | provider keys | the deployment | deployment-managed | environment only, via `CredentialProvider` (ADR-0049) |

**Invariants**

* **S1.** No browser ever holds an org API key or a provider credential. The console
  authenticates with a cookie. The Playground peer holds only a signalling token.
* **S2.** No Epic 12 response, log line or error message contains a provider credential, an API
  key hash, a session token or a password hash. A raw API key is returned **once**, at creation.
* **S3.** Every console query that touches tenant data filters by the org resolved
  **server-side** from the session, never from a client-supplied org id.
* **S4.** A resource in another org is indistinguishable from a missing one (404).
* **S5.** Revocation is immediate. A revoked API key's `apikey` row is gone, and a revoked console
  session fails its next request.
* **S6 (M12.7).** A raw developer API key exists in exactly one HTTP response: the `201` of its
  creation, marked `Cache-Control: no-store`. The console holds it only in component state until
  the user dismisses it. It never writes it to storage, a URL or history, and nothing can fetch it
  again. Key management is owner/admin only, including listing.

## 3. Authentication details

* **Passwords:** stdlib `hashlib.scrypt` (N=2¹⁴, r=8, p=1, 16-byte salt, 64-byte key). Minimum
  length 12. Comparison uses `hmac.compare_digest`. Unknown email and wrong password return the
  same response and take the same code path (a dummy hash is verified for unknown emails).
* **Sessions:** 32-byte `secrets.token_urlsafe`. The token is rotated on login and has no other
  refresh path. Logout revokes.
* **Login throttling:** a per-email and per-client limit. In-process (Stage 1). Recorded as a
  Stage-2 item.

## 4. Browser security

* **Same origin by construction.** Dev: Vite proxies `/v1` and `/health` to the API. Local
  product mode: the API serves the bundle under `/console/`. No CORS is needed, and none is added
  for the console. `WEBRTC_ALLOWED_ORIGINS` remains M11's setting for M11's routes.
* **Cookie:** `HttpOnly; SameSite=Strict; Path=/`, and `Secure` when `APP_ENV != dev`.
* **CSRF:** mutating console routes require `x-voxvoice-console: 1`. `SameSite=Strict` plus a
  required custom header, which cross-origin requests cannot send without a preflight the server
  never grants, prevents cross-site writes.
* **XSS posture:** React escapes by default. The console never uses `dangerouslySetInnerHTML`, and
  it never renders transcript or agent text as HTML. No token is stored in `localStorage` or
  `sessionStorage`. Browser storage holds UI preferences only.
* **Microphone:** requested only on explicit user action in the Playground. It needs a secure
  context: `localhost` works, and a LAN IP does not (local-run §7).

## 5. Provider credentials never reach the frontend

Provider keys are read only by `EnvCredentialProvider` inside the composition root. Epic 12 code:

* never imports `api.adapters.secrets` outside the composition path it inherits from M11;
* never exposes provider names, keys or voice ids through `/v1/console/meta`. The runtime section
  is limited to transport, signalling and the ICE description, which is already public on
  `/health`;
* has tests that plant sentinel values in `VOXVOICE_SECRET_*` and assert they appear in no console
  response.

## 6. Authorization & tenant isolation tests (required per milestone)

| Test | Milestone |
|---|---|
| Console meta never leaks planted secrets | **M12.1** |
| M11 route error bodies are unchanged under the console app | **M12.1** |
| Console bundle serving refuses path traversal | **M12.1** |
| Unauthenticated console calls → 401 envelope | **M12.2 ✅** |
| A user of org A reads/edits an org B agent → 404 | **M12.3 ✅** (read, edit, archive; org id in a request body is ignored) |
| A user of org A starts a Playground session on an org B agent → 404, and no session row is created | **M12.4 ✅** (also archived, missing and undeclared-language agents, foreign rendezvous, foreign session end; the browser receives only `{rendezvous_id, signalling_token, expires_in_s}`) |
| A user of org A reads an org B conversation or events → 404 | **M12.5 ✅** (list and detail; events filed under a foreign `org_id` with the same session id are excluded; raw payloads are never returned) |
| Analytics never aggregate across orgs | **M12.6 ✅** (all four endpoints: counts, turns, series and per-agent figures of org A exclude org B, including events filed under the wrong `org_id` in either direction. Another org's `agent_id` gets the same 404 as a missing one. An `org_id`/`organization_id` query parameter is ignored. Every role reads, and a signed-out caller gets 401) |
| A revoked API key → 401 on the developer API | **M12.7 ✅** (immediately, server-side. Also: the raw key is in no list, error, log or DB column, only its SHA-256 is stored, a foreign key revoke is 404, an org A key reads no org B agent or session in either direction, missing and malformed keys are 401, the console cookie does not open the developer API and a key does not open the console, only owner/admin manage keys, and CSRF is enforced; live HTTP smoke too) |
| The viewer role cannot mutate. Mutation without the CSRF header → 403 | M12.2 ✅ (org rename, CSRF) → M12.7 |

## 7. Enforced as of M12.2

* **S1**: the console authenticates with the `vv_console` cookie only. The console makes no
  `localStorage`/`sessionStorage` write (a console test spies on `Storage.setItem`). The only
  browser-held secret is the `HttpOnly` cookie, which JavaScript cannot read.
* **S2**: responses are scanned in tests for `scrypt$`, the session token and the word
  `password`. The database holds only scrypt hashes and SHA-256 token hashes, as verified against
  a live database.
* **S3**: the active organization exists only on the server-side session. No read takes a client
  org id (tested with a spoofed `?org_id=`).
* **S4**: selecting another tenant's organization and selecting a non-existent one produce the
  same 404.
* **S5 (console sessions)**: sign-out revokes server-side, and a copied cookie is refused
  afterwards. Absolute and idle expiry, and disabling a user, all end live sessions. API-key
  revocation is M12.7.
* **CSRF**: every non-GET console route refuses a request without `x-voxvoice-console: 1`.
* **Throttling**: per `(email, client)` in-process. It is not shared across processes, which is
  recorded as a Stage-2 item.
* **Credential separation**: a console session does not authenticate `/v1/agents`, and an
  `x-api-key` does not authenticate `/v1/console/*` (both tested).

The M12.1 obligations (meta secret-free, M11 error bodies unchanged, traversal refused) remain
tested.
