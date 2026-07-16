# Research Archive — Dograh Engine API (verified knowledge)

| | |
|---|---|
| **Status** | Archived reference · **Owner:** Technical Owner |
| **Date archived** | 2026-07-16 (milestone M0) |
| **Why archived** | ADR-0001 retires the whole-engine adapter axis; ADR-0007 deletes `platform/adapters/` at migration M1. Per Constitution D7 / ARCHITECTURE_RULES AR-54, the *code* is removed but the hard-won, live-verified *knowledge* is preserved here so it is not re-learned from scratch if a Dograh integration is ever reconsidered as an optional degraded-mode plugin (ADR-0001 permits that; the core path does not use it). |
| **Provenance** | Verified against a live self-hosted **Dograh v1.35.0** (Docker), 2026-06-15, by exercising each route against the running instance — not assumed. Source: the now-retired `platform/adapters/dograh.py` and `platform/README.md`. |

## Concept mapping (VoxVoice → Dograh)

| VoxVoice concept | Verified Dograh API |
|---|---|
| Auth | `X-API-Key: dgr_…` (key from `POST /api/v1/user/api-keys`). **Not** `Authorization: Bearer` — that is the login JWT, not the API key. |
| Create agent | `POST /api/v1/workflow/create/definition {name, workflow_definition}` → `{id}` |
| Start web session | `POST /api/v1/workflow/{id}/runs {mode:"webrtc", name}` → `{id}` (the run id) |
| Web connect credentials | `POST /api/v1/workflow/{id}/embed-token` → `{token:"emb_…", embed_script, expires_at}`, plus TURN creds via `GET /api/v1/public/embed/turn-credentials/{token}` |
| Transcript | run report `GET /api/v1/workflow/{id}/runs/{run_id}` — fields include `is_completed`, `transcript_url`, `transcript_public_url`, `recording_url`. **No transcript-push webhook exists** (only telephony status callbacks). |
| Phone (never built) | `POST /api/v1/public/agent/workflow/{workflow_uuid}` |
| API prefix | `/api/v1` (health: `GET /api/v1/health`) |

## Key deviations from the original assumptions (why renting was blind)

1. **No `token+url+room` web contract.** The originally-assumed `/api/v1/calls/web` returning `{token,url,room}` was WRONG. Dograh's real model is **workflow / run / embed-token**; web connection is an embed token + `embed_script` + separate TURN creds over WebRTC. There is no LiveKit-style room/url.
2. **A real conversation needs a node graph.** `POST /workflow/create/definition` accepts a flat config, but a minimal definition will not actually converse. A runnable agent needs a React-Flow node graph: `globalNode` (persona/system prompt) → `startCall` (greeting/first message) → `agentNode` (conversation) → `endCall`. This exact shape passed `POST /workflow/{id}/validate` (`is_valid: true`) and produced a runnable `mode:"webrtc"` run on v1.35.0. Edges require `data:{label, condition}` where the condition is an LLM-evaluated free-text string — i.e. conversation *progression is non-deterministic by construction* and unobservable to the caller.
3. **No transcript push → polling only.** Because Dograh exposes no transcript webhook, transcripts could only be obtained by polling the run report after the call — the platform had zero live visibility into an in-progress conversation. (This is a primary driver of ADR-0001; see the Phase 1.5 voice-runtime audit.)
4. **No documented "end run" route.** A webrtc run ends when the browser disconnects the peer connection; there was no REST call to end a call early.

## Operational notes (local Dograh, if ever needed for reference)

- Official compose maps the API to host `:8000` and UI/embed host to `:3010`; if `:8000` is taken, remap the api service to `8010:8000` and set `BACKEND_API_ENDPOINT=http://localhost:8010`.
- **Local voice does not work on macOS Docker** (no TURN relay IP reachable by both the host browser and the in-container bot). A public-IP host resolves it. Full runbooks were in `platform/DEPLOY.md` / `platform/ORACLE_DEPLOY.md` (migrate to `docs/deployment/` at M1 if retained).

## Status of this integration

**Retired from the core path** (ADR-0001, ADR-0007). Not a VoxVoice runtime. If reconsidered later, it would be an optional, capability-limited (no live transcript, no mid-call control) integration plugin — never the differentiated product path.
