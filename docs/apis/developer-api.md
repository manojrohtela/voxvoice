# VoxVoice Developer API

| | |
|---|---|
| **Status** | Living · M12.7 · **the external developer contract** that the M12.9 SDK will be built from |
| **Date** | 2026-09-21 |
| **Version** | `v1` (every path starts with `/v1`) |
| **Machine-readable** | `GET /openapi.json` on your VoxVoice server. Operations whose `x-voxvoice-surface` is `developer` or `signalling` are this API. Everything else is the first-party console API or public health/metadata. |
| **Authority** | [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP4/DP7, Amendment 3 (DP13) |

This page describes what the API does **today**. Anything not listed here is not part of the API,
and §11 lists what is deliberately not available yet.

## 1. Credentials

VoxVoice has three credentials. Only the first is yours to use with this API.

| Credential | Who holds it | Sent as | What it can do |
|---|---|---|---|
| **Developer API key** | your backend server | header `x-api-key: <YOUR_API_KEY>` | everything in §4–§6, for **one organization** |
| **Signalling token** | a caller's browser, for one call | header `x-signalling-token: <SIGNALLING_TOKEN>` | exchange one WebRTC offer/answer for one session. Nothing else. |
| Console session | a person signed in to the VoxVoice console | cookie (browser only) | the console. **It does not work on this API**, and your API key does not work on the console. |

**A VoxVoice API key is not a speech, language-model or voice-provider key.** VoxVoice's operator
configures those providers on the server. You never need, send or see a provider credential, and
no endpoint returns one.

## 2. API keys

### Create a key
1. Sign in to the console as an organization **owner** or **admin**.
2. Open **API keys** and choose **Create key**. Give it a label that says where it will be used,
   for example `billing-backend`.
3. The key is shown **once**. Copy it into your secret store (an environment variable or a secrets
   manager) before you close the dialog. VoxVoice keeps only a one-way hash, so nobody can show it
   again. If it is lost, create a new key and revoke the old one.

A key looks like `vf_` followed by 43 URL-safe characters. Its **id** (`key_…`) is what the console
lists, and it is not secret. The id cannot authenticate anything.

### Keep it secret
* Use it only from your server. **Never** put it in browser JavaScript, a mobile app, a URL or a
  log. To connect a caller's browser, use a signalling token (§6).
* Anyone holding the key can act as your organization on this API, until the key is revoked.

### Revoke a key
**API keys → Revoke** (owner or admin). Revocation is immediate: the next request with that key
gets `401 {"detail": "Invalid API key"}`. A revoked key cannot be restored.

### What a key does and does not grant
* **Grants:** the agents and sessions of the organization that created it, and nothing of any other
  organization. Another organization's agent or session behaves exactly as if it did not exist
  (`404`).
* **Does not grant:** console sign-in, console-only views (Conversations, Analytics, member and key
  management), other organizations, or any provider account.
* **There are no scopes.** Every key of an organization has the same access.

## 3. Conventions

| | |
|---|---|
| **Base URL** | your VoxVoice server, e.g. `http://localhost:8000` when running locally |
| **Format** | JSON request and response bodies (`Content-Type: application/json`) |
| **IDs** | opaque strings with a type prefix: `org_…`, `agt_…` (agent), `ses_…` (session), `rdv_…` (rendezvous). Treat them as opaque; do not parse them. |
| **Timestamps** | ISO 8601 in **UTC, without an offset**, e.g. `2026-09-21T12:27:16.405839`. Read them as UTC. |
| **Nullable fields** | returned as `null`, never omitted. The schemas below mark them `\| null`. |
| **Pagination** | **none.** `GET /v1/agents` returns all of the organization's agents as a JSON array. No other endpoint lists. |
| **Rate limiting** | **none** on this API today. The only `429` is the per-server cap on open WebRTC rendezvous (§6). |
| **Versioning** | additive-only within `/v1`: new fields and endpoints may appear, and existing ones do not change meaning. Ignore fields you do not know. |

## 4. Agents

An **agent** is a voice agent: its instructions, greeting and language. Sessions run an agent.

| Method | Path | Success |
|---|---|---|
| `POST` | `/v1/agents` | `201` agent |
| `GET` | `/v1/agents` | `200` array of agents (unpaginated) |
| `GET` | `/v1/agents/{agent_id}` | `200` agent |
| `PATCH` | `/v1/agents/{agent_id}` | `200` agent (only the fields you send change) |
| `DELETE` | `/v1/agents/{agent_id}` | `200` `{"deleted": "<agent_id>"}` |

**Agent** (response):

```json
{
  "id": "agt_48e215e62a4b430a70b97588",
  "org_id": "org_566f6b35ff13a727bf9b5795",
  "name": "Support",
  "system_prompt": "You are a helpful support agent.",
  "first_message": "Hi! How can I help?",
  "language": "en",
  "stt_provider": "default",
  "tts_provider": "default",
  "tts_voice": null,
  "llm_model": null,
  "allow_interruptions": true,
  "min_endpointing_delay": 0.5,
  "max_endpointing_delay": 6.0,
  "tools": [],
  "metadata": {},
  "created_at": "2026-09-21T12:27:16.405839",
  "updated_at": "2026-09-21T12:27:16.405847"
}
```

**Create** requires `name` and `system_prompt`. Every other field is optional:

| Field | Type | Default | Effect today |
|---|---|---|---|
| `name` | string | required | display name |
| `system_prompt` | string | required | the agent's instructions |
| `first_message` | string \| null | `null` | spoken when a call connects. `null` means the agent waits for the caller. |
| `language` | string | `"en-IN"` | **Set this explicitly.** The server's speech recognizer currently supports `en`. The default `en-IN` is accepted, but calls with it drop after the greeting (known issue, not yet fixed in the API). |
| `llm_model` | string \| null | `null` | a model id the server's language-model provider offers. `null` uses the server default. |
| `tts_voice` | string \| null | `null` | a voice id the server's voice provider offers. `null` uses the server default. |
| `stt_provider`, `tts_provider` | string | `"default"` | **Leave as `"default"`.** Providers are chosen by the server operator. Naming one the server does not have makes session start fail with `502`. |
| `metadata` | object | `{}` | yours: stored and returned unchanged |
| `allow_interruptions`, `min_endpointing_delay`, `max_endpointing_delay`, `tools` | | | **Stored and returned, but not applied to calls today.** In particular, callers cannot interrupt the agent while it is speaking (barge-in is not available yet). |

**Delete** removes the agent permanently. Deleting an agent that already has sessions is not
supported on every database. Prefer keeping agents you have used.

## 5. Sessions

A **session** is one conversation between a caller and an agent.

| Method | Path | Success |
|---|---|---|
| `POST` | `/v1/sessions` | `201` session-created |
| `GET` | `/v1/sessions/{session_id}` | `200` session |
| `GET` | `/v1/sessions/{session_id}/transcript` | `200` array of transcript items |
| `POST` | `/v1/sessions/{session_id}/end` | `200` `{"session_id": "…", "status": "ended"}` (idempotent) |

**Start:** `POST /v1/sessions` with `{"agent_id": "agt_…", "channel": "web", "metadata": {…}}`.
`channel` defaults to `"web"`, and `metadata` is yours. The agent must belong to your organization.

```json
{"session_id": "ses_0e4f36c46805bb80c4e7a855", "engine": "voxvoice", "channel": "web", "web": null, "phone": null}
```

`web` and `phone` are currently always `null`: this call connects no caller. What it does depends on
the server:

* **voice enabled** (the real WebRTC transport): there is nothing for the runtime to connect to, so
  the call **fails with `502 "Runtime failed to start the session"`** and the session is recorded as
  `failed`. Start voice sessions with **`POST /v1/webrtc/sessions`** (§6).
* **voice not enabled**: it starts a session with no audio path, which is useful for exercising your
  integration.

**Session** (`GET /v1/sessions/{id}`):

```json
{
  "id": "ses_0e4f36c46805bb80c4e7a855",
  "org_id": "org_566f6b35ff13a727bf9b5795",
  "agent_id": "agt_48e215e62a4b430a70b97588",
  "engine": "voxvoice",
  "engine_ref": "ses_0e4f36c46805bb80c4e7a855",
  "channel": "web",
  "status": "active",
  "started_at": "2026-09-21T12:27:16.428926",
  "ended_at": null,
  "metadata": {"customer_ref": "c-42"}
}
```

`status` is one of `active` (running), `ended` (ended through `/end`) and `failed` (the runtime
could not start it; the start call returned `502`). `ended_at` is `null` until the session ends. The
session does **not** record why a call ended.

**Transcript** (`GET /v1/sessions/{id}/transcript`): final lines only, as
`[{"session_id", "role": "user" | "agent", "text", "ts"}]`, ordered by `ts`, which is seconds since
the session started. Sessions recorded by servers older than M12.5 have `ts` `0.0` on every line,
and their order is not guaranteed.

**End:** `POST /v1/sessions/{id}/end` stops the conversation and releases its resources. Ending an
ended session is a no-op that returns the same body.

**Listing sessions is not available** through this API.

## 6. Connecting a caller's browser (WebRTC)

Available only when the server has voice enabled. Otherwise these paths return `404`.

| Method | Path | Credential | Success |
|---|---|---|---|
| `POST` | `/v1/webrtc/rendezvous` | API key | `201` `{"rendezvous_id", "signalling_token", "expires_in_s"}` |
| `POST` | `/v1/webrtc/sessions` | API key | `201` session-created (as §5), once the caller is connected |
| `GET` | `/v1/webrtc/rendezvous/{rendezvous_id}/offer` | signalling token | `200` `{"sdp": "…"}`, or `204` = not ready yet, ask again |
| `POST` | `/v1/webrtc/rendezvous/{rendezvous_id}/answer` | signalling token | `204` |

The flow. Your API key never leaves your server:

1. **Your server** calls `POST /v1/webrtc/rendezvous` and passes `rendezvous_id` and
   `signalling_token` to the caller's browser. The token expires after `expires_in_s` seconds.
2. **Your server** calls `POST /v1/webrtc/sessions` with `{"agent_id": "agt_…", "rendezvous_id": "rdv_…"}`.
   The call **waits until the browser is connected**, so make it without blocking anything else.
3. **At the same time, the browser** polls `GET …/offer` (with `x-signalling-token`) until it gets
   `200`. It then creates its WebRTC answer and sends it with `POST …/answer` `{"sdp": "…"}`.
4. When the connection is up, step 2 returns `201` with the `session_id`. End the call with
   `POST /v1/sessions/{session_id}/end`.

A browser on a different origin from the API needs that origin allowed by the server's operator
(CORS).

## 7. Errors

The developer API returns `{"detail": "<message>"}`, or for validation errors
`{"detail": [{"type", "loc", "msg", "input"}]}`. Status codes are the contract. Messages are for
people and may change.

| Status | When | Example `detail` |
|---|---|---|
| `401` | missing or invalid API key (including a revoked one) | `"Missing x-api-key"`, `"Invalid API key"` |
| `401` | signalling: wrong token for an existing rendezvous | `"invalid signalling token"` |
| `404` | the agent or session does not exist **or belongs to another organization**; the two are indistinguishable | `"Agent not found"`, `"Session not found"` |
| `404` | signalling: unknown or expired rendezvous | `"no such signalling rendezvous"` |
| `409` | signalling: the rendezvous is not in a state that allows this step (already used, closed) | `"rendezvous is bound, not reserved"` |
| `413` | signalling: an SDP larger than the server accepts | |
| `422` | the request body or parameters are invalid | `[{"type": "missing", "loc": ["body", "system_prompt"], "msg": "Field required", …}]` |
| `429` | too many WebRTC rendezvous open on the server at once (a capacity limit, not a rate limit). Retry later. | `"too many open signalling rendezvous (…)"` |
| `502` | the runtime could not start the session (the session is recorded as `failed`) | `"Runtime failed to start the session"` |
| `503` | the server has no runtime configured | `"Runtime Plane is not configured"` |
| `504` | WebRTC: the browser did not answer in time | `"the peer did not answer within …s"` |
| `500` | an unexpected server error | |

The API does not return `403` for your API key: a key either identifies your organization (and its
resources) or it is rejected with `401`.

## 8. Examples

Replace `<YOUR_API_KEY>` with your key, read from your secret store. Never hard-code it.

**curl**

```bash
export VOXVOICE_API_KEY="<YOUR_API_KEY>"
BASE=http://localhost:8000

# Create an agent
curl -sS -X POST "$BASE/v1/agents" \
  -H "x-api-key: $VOXVOICE_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Support", "system_prompt": "You are a helpful support agent.", "first_message": "Hi! How can I help?", "language": "en"}'

# List agents
curl -sS "$BASE/v1/agents" -H "x-api-key: $VOXVOICE_API_KEY"

# Start, inspect and end a session
curl -sS -X POST "$BASE/v1/sessions" -H "x-api-key: $VOXVOICE_API_KEY" \
  -H "Content-Type: application/json" -d '{"agent_id": "agt_…"}'
curl -sS "$BASE/v1/sessions/ses_…" -H "x-api-key: $VOXVOICE_API_KEY"
curl -sS -X POST "$BASE/v1/sessions/ses_…/end" -H "x-api-key: $VOXVOICE_API_KEY"
```

**Python** (any HTTP client; `httpx` shown)

```python
import os

import httpx

client = httpx.Client(
    base_url="http://localhost:8000",
    headers={"x-api-key": os.environ["VOXVOICE_API_KEY"]},
)

agent = client.post(
    "/v1/agents",
    json={"name": "Support", "system_prompt": "You are a helpful support agent.", "language": "en"},
).json()
session = client.post("/v1/sessions", json={"agent_id": agent["id"]}).json()
print(client.get(f"/v1/sessions/{session['session_id']}").json()["status"])
client.post(f"/v1/sessions/{session['session_id']}/end")
```

**TypeScript** (server-side, Node 18+)

```ts
const BASE = "http://localhost:8000";
const headers = { "x-api-key": process.env.VOXVOICE_API_KEY!, "Content-Type": "application/json" };

const agent = await fetch(`${BASE}/v1/agents`, {
  method: "POST",
  headers,
  body: JSON.stringify({ name: "Support", system_prompt: "You are a helpful support agent.", language: "en" }),
}).then((r) => r.json());

const res = await fetch(`${BASE}/v1/sessions`, { method: "POST", headers, body: JSON.stringify({ agent_id: agent.id }) });
if (!res.ok) throw new Error(`VoxVoice ${res.status}: ${JSON.stringify(await res.json())}`);
const { session_id } = await res.json();
```

## 8a. Using the TypeScript SDK

`@voxvoice/sdk` (in [`sdk/`](../../sdk/README.md)) is a thin, typed client for exactly the API on
this page: the same endpoints, fields, `null`s and timestamps, and the same errors as a
`VoxVoiceError`. It is for **Node.js 20.3+ on your server**, and it holds your API key. It is not
published to a registry yet: `npm install /path/to/VoxVoice/sdk`.

```ts
import { VoxVoiceClient, VoxVoiceError } from "@voxvoice/sdk";

const client = new VoxVoiceClient({
  apiKey: process.env.VOXVOICE_API_KEY!, // never hard-code it, never send it to a browser
  baseUrl: "http://localhost:8000",      // without /v1
});

const agent = await client.agents.create({ name: "Support", system_prompt: "Be brief.", language: "en" });
const { session_id } = await client.sessions.create({ agent_id: agent.id });
try {
  await client.sessions.get("ses_missing");
} catch (error) {
  if (error instanceof VoxVoiceError) console.error(error.status, error.message); // 404 Session not found
}
await client.sessions.end(session_id);
```

| SDK | API |
|---|---|
| `agents.create / list / get / update / delete` | §4 |
| `sessions.create / get / transcript / end` | §5 |
| `webrtc.createRendezvous / startSession` (API key, your server) | §6 steps 1–2 |
| `SignallingClient.getOffer / sendAnswer` from `@voxvoice/sdk/signalling` (signalling token, the browser) | §6 step 3 |

* The SDK **does not implement WebRTC**. The browser's `RTCPeerConnection` does, and the SDK only
  carries the offer and answer.
* **Nothing is retried** (the API has no idempotency keys), and there is no default timeout.
  Every method accepts `{signal, timeoutMs}`.
* Everything in §11 applies to the SDK too. It adds no capability the API lacks.

## 9. Conversations and analytics

Conversation history (transcript, per-turn latency, timeline) and analytics are available **in the
console** only. They are not part of this API. Through the API you can read a session
(`GET /v1/sessions/{id}`) and its transcript (`GET /v1/sessions/{id}/transcript`).

## 10. Webhooks

When a session starts or ends, the server may POST `session.started` / `session.ended` to a webhook
URL configured **by the server operator**. The webhook URL cannot be set through this API.

## 11. Not available yet

Stated plainly, so nothing here is assumed:

* key scopes, expiry and rotation (create a new key, then revoke the old one); rate limits
* pagination; listing sessions; searching
* conversations, analytics, cost, token or usage figures, and end reasons, through the API
* interrupting the agent while it speaks (barge-in); `allow_interruptions`, the endpointing delays
  and `tools` have no effect yet
* phone channels (`phone` is always `null`)
* a high-level voice-call helper in the SDK (it carries signalling only; §8a)
* a hosted VoxVoice service: today you run the server yourself

## 12. Reference

The endpoint reference, with every schema, is generated from the running server:
`GET /openapi.json`, or the **Developer docs** page in the console. The operations listed in §4–§6
are exactly the operations marked `x-voxvoice-surface: developer` or `signalling` there (enforced by
a test).
