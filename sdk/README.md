# @voxvoice/sdk

A small, typed TypeScript client for the **VoxVoice developer API (v1)**: agents, sessions, and
the server side of connecting a caller over WebRTC. It is a thin client over the HTTP API
documented in [`docs/apis/developer-api.md`](../docs/apis/developer-api.md): the same endpoints,
fields and errors. It adds no features the API does not have.

* **Runtime:** Node.js 20.3 or later (uses the built-in `fetch`). ESM only. No runtime dependencies.
* **Version:** `0.1.0`. Pre-1.0, so a minor version may change the public API. From 1.0: breaking
  changes → major, additions → minor, fixes → patch.
* **Not published to a registry yet.** Install it from this repository.

## Install

```bash
# from your project, pointing at a checkout of this repository
npm install /path/to/VoxVoice/sdk
# or build a tarball: cd sdk && npm ci && npm run build && npm pack
```

## Quick start (server-side)

```ts
import { VoxVoiceClient, VoxVoiceError } from "@voxvoice/sdk";

const client = new VoxVoiceClient({
  apiKey: process.env.VOXVOICE_API_KEY!, // from your environment or secret manager
  baseUrl: "http://localhost:8000",      // your VoxVoice server, without /v1
});

const agent = await client.agents.create({
  name: "Support",
  system_prompt: "You are a helpful support agent.",
  first_message: "Hi! How can I help?",
  language: "en", // set it: the API default "en-IN" is not supported by the recognizer today
});

const session = await client.sessions.create({ agent_id: agent.id });
console.log((await client.sessions.get(session.session_id)).status); // "active"
await client.sessions.end(session.session_id);
```

## Authentication and security

The client sends your organization's **API key** as `x-api-key` on every request, and nowhere
else: never in a URL, a log or an error. It keeps the key in memory for the life of the client.

**Server-side only.** An API key grants access to everything your organization has on the API.
Never put it in browser code, a mobile app, a public environment variable or a URL. To connect a
caller's browser, hand it a **signalling token** instead (below). That token can do one thing, for
one call, and it expires.

An API key is not a speech, language-model or voice-provider key. You never need those.

## Resources

| Method | HTTP |
|---|---|
| `agents.create(body)` | `POST /v1/agents` |
| `agents.list()` | `GET /v1/agents`: all agents, as an array (the API is not paginated) |
| `agents.get(id)` | `GET /v1/agents/{id}` |
| `agents.update(id, body)` | `PATCH /v1/agents/{id}`: only the fields you send change. `null` clears `first_message`, `tts_voice`, `llm_model`. |
| `agents.delete(id)` | `DELETE /v1/agents/{id}`: permanent |
| `sessions.create(body)` | `POST /v1/sessions`: no caller can join this session (see WebRTC) |
| `sessions.get(id)` | `GET /v1/sessions/{id}` |
| `sessions.transcript(id)` | `GET /v1/sessions/{id}/transcript` |
| `sessions.end(id)` | `POST /v1/sessions/{id}/end`: safe to repeat |
| `webrtc.createRendezvous()` | `POST /v1/webrtc/rendezvous` |
| `webrtc.startSession(body)` | `POST /v1/webrtc/sessions`: resolves once the caller is connected |

Every method also takes `{ signal?: AbortSignal, timeoutMs?: number }`. The client accepts a default
`timeoutMs`; there is none unless you set one.

Responses are returned **as the server sends them**. `null` stays `null`. Timestamps stay strings:
ISO 8601 in UTC **without an offset** (`"2026-09-21T12:27:16.405839"`), so read them as UTC. IDs
are opaque strings (`agt_…`, `ses_…`, `rdv_…`).

## Errors

Every failure is a `VoxVoiceError`, except cancellation, which is the platform's `AbortError`:

```ts
try {
  await client.agents.get("agt_missing");
} catch (error) {
  if (error instanceof VoxVoiceError) {
    error.status;  // 404, or null if no response arrived (network failure, timeout)
    error.detail;  // the server's detail: a string, or the 422 validation list
    error.message; // "Agent not found"
  }
}
```

A resource of another organization is a `404`, indistinguishable from a missing one. A missing,
invalid or revoked key is a `401`. The developer API returns no error code and no request id, so
the error has neither.

**Nothing is retried.** A request that fails, fails once, so a create is never sent twice. The API
has no idempotency keys, so retrying a mutation after a network failure may repeat it.

## Connecting a caller's browser (WebRTC, low level)

Available when the server has voice enabled. The SDK carries the signalling messages. It does
**not** implement WebRTC: the browser's own `RTCPeerConnection` does that.

```ts
// Your server (API key):
const { rendezvous_id, signalling_token } = await client.webrtc.createRendezvous();
// → send rendezvous_id + signalling_token to the caller's browser, then (without blocking on it):
const started = client.webrtc.startSession({ agent_id: agent.id, rendezvous_id });
```

```ts
// The browser (signalling token only — never the API key):
import { SignallingClient } from "@voxvoice/sdk/signalling";

const signalling = new SignallingClient({ baseUrl, rendezvousId, signallingToken });
let offer = null;
while (!(offer = await signalling.getOffer())) {} // null = not ready yet; each call waits briefly
const peer = new RTCPeerConnection();
// add the microphone track, then:
await peer.setRemoteDescription({ type: "offer", sdp: offer.sdp });
await peer.setLocalDescription(await peer.createAnswer());
// wait for ICE gathering to complete (the server expects a complete answer), then:
await signalling.sendAnswer(peer.localDescription!.sdp);
```

A browser on a different origin from the API needs that origin allowed by the server operator
(CORS).

## Not available yet

These are stated so that nothing is assumed:
- Conversations and analytics: console only.
- Key scopes, expiry and rotation. Rate limits (none exist).
- Pagination. Listing sessions.
- Barge-in: `allow_interruptions`, the endpointing delays and `tools` are stored but have no effect.
- End reasons. Phone channels (`web`/`phone` are always `null`).
- A high-level "voice call" helper, retries, webhooks and streaming.

## Development

```bash
npm ci
npm run typecheck && npm test   # unit + contract tests (HTTP mocked at fetch)
npm run test:package            # builds, then imports the built package by name
```

`contract/developer-openapi.json` is the server's OpenAPI for the developer surface, regenerated
with `python -m api.console.developer_contract` from `platform/`. The contract tests hold every
method and type to it, and a platform test fails if it is stale.
