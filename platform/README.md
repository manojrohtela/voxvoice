# VoxVoice Platform — Phase 1 (API in front)

Self-hostable, context-driven voice-agent platform (a Vapi alternative). The
voice agent is the product; other apps (VoxHire, a future sales tool) are
*consumers* that call this API. **Own the interface, rent the engine** — the
orchestration engine (Dograh primary, LiveKit fallback) sits behind a swappable
`EngineAdapter`; no consumer code touches the engine.

> Full design: [`../docs/SPEC.md`](../docs/SPEC.md). This README covers running
> Phase 1 only. Phases 2–4 (Hinglish/Sarvam, KB, tools, telephony, analytics,
> consumer SDKs) are intentionally **not** built yet.

## What's in Phase 1

- FastAPI app + SQLModel models (Org, ApiKey, Agent, Session, Transcript).
- Org-scoped API-key auth (`x-api-key`); only key hashes are stored.
- Full agents CRUD + sessions endpoints (start web session, end, get, transcript).
- `EngineAdapter` ABC, `DograhAdapter` (web sessions), `LiveKitAdapter` stub.
- Transcript persistence + signed webhook fan-out (HMAC-SHA256, `x-signature`).
- Config-driven engine swap (`ENGINE=dograh|livekit`); `.env.example`; tests.

## Layout

```
platform/
  api/        FastAPI app — config, db, models, schemas, security, routers, ingest
  adapters/   engine_adapter (base) + dograh + livekit + factory
  webhooks/   signing + dispatch
  tests/      pytest: auth scoping, CRUD, session flow, adapter contract
```

> The directory is named `platform` per SPEC §8 but is the **project root**, not
> a Python package (it would shadow the stdlib `platform` module). Run all
> commands from inside `platform/`; top-level packages are `api`, `adapters`,
> `webhooks`.

## Run

```bash
cd platform
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in DOGRAH_BASE_URL, ENGINE_CALLBACK_SECRET, ...

# Provision an org + its first API key (raw key is shown once):
python -m api.bootstrap "Acme Corp" --label dev

# Serve:
uvicorn api.main:app --reload --port 8000
```

`GET /health` → `{ "ok", "engine", "db" }`. Interactive docs at `/docs`.

### Example consumer flow

```bash
KEY=vf_...   # from `python -m api.bootstrap`

# Create an agent
curl -sX POST localhost:8000/v1/agents -H "x-api-key: $KEY" \
  -H 'content-type: application/json' \
  -d '{"name":"Reschedule bot","system_prompt":"You reschedule interviews.","language":"hinglish"}'

# Start a web session -> returns { session_id, web: { token, url, room } }
curl -sX POST localhost:8000/v1/sessions -H "x-api-key: $KEY" \
  -H 'content-type: application/json' -d '{"agent_id":"agt_...","metadata":{"candidate":"123"}}'

# Read the transcript
curl -s localhost:8000/v1/sessions/ses_.../transcript -H "x-api-key: $KEY"
```

The consumer connects the call with the returned `web` details via the engine's
web client (the Phase 4 TypeScript SDK will wrap this so consumers never import
Dograh).

## Tests

```bash
cd platform && pytest          # 13 tests: auth scoping, CRUD, session flow, adapter contract
ruff check .
```

Tests run the full session flow against an in-memory `FakeAdapter` (conforms to
the adapter contract), so no live Dograh instance is required for CI.

## Engine swap

Set `ENGINE` in `.env`:
- `dograh` → `DograhAdapter` (primary, web sessions).
- `livekit` → `LiveKitAdapter` (stub; every method raises `NotImplementedError`
  until/unless LiveKit becomes primary, SPEC §10 decision 1).

Only `adapters/factory.py` knows which concrete engine exists. The API and
consumers depend solely on `EngineAdapter`.

## How transcripts flow

```
Dograh --POST /v1/engine/dograh/events (x-engine-secret)--> platform
  -> adapter.dispatch_transcript -> registered handler
  -> persist Transcript row -> emit `transcript.item` webhook to the Org
```

The ingestion endpoint is engine-facing (not org-scoped) and guarded by
`ENGINE_CALLBACK_SECRET`.

## ✅ Dograh integration — VERIFIED against v1.35.0 (2026-06-15)

The `DograhAdapter` was exercised against a live self-hosted Dograh (Docker).
The spec's *assumed* routes (`/api/v1/calls/web`, a token+url+room response) were
**wrong**; Dograh's real model is workflow/run/embed-token based. Verified facts
now encoded in [`adapters/dograh.py`](adapters/dograh.py):

| Our concept | Verified Dograh API |
|---|---|
| Auth | `X-API-Key: dgr_…` (from `POST /api/v1/user/api-keys`; **not** Bearer — that's the login JWT) |
| Create Agent | `POST /api/v1/workflow/create/definition {name, workflow_definition}` → `{id}` |
| Start web Session | `POST /api/v1/workflow/{id}/runs {mode:"webrtc", name}` |
| Web connect creds | `POST /api/v1/workflow/{id}/embed-token` → `{token:"emb_…", embed_script, expires_at}` + TURN creds via `GET /api/v1/public/embed/turn-credentials/{token}` |
| Transcript | run report `GET /api/v1/workflow/{id}/runs/{run_id}` (**no push webhook**) |
| Phone (Phase 3) | `POST /api/v1/public/agent/workflow/{workflow_uuid}` |

### Run Dograh locally

```bash
curl -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml
curl -o start_docker.sh https://raw.githubusercontent.com/dograh-hq/dograh/main/scripts/start_docker.sh
chmod +x start_docker.sh && ./start_docker.sh      # UI :3010, API :8000
```
> If host port **8000** is taken, remap the api service to `8010:8000` in
> `docker-compose.yaml` and set `BACKEND_API_ENDPOINT=http://localhost:8010`
> (this is how it was verified here). Then create an API key in the UI
> (Settings → API keys) or via `POST /api/v1/user/api-keys`, and set
> `DOGRAH_API_KEY`, `DOGRAH_BASE_URL`, `DOGRAH_UI_BASE_URL` in `.env`.

### Status of the previously-open items (now BUILT)

1. ✅ **WEB-CONTRACT (owner decision: superset).** `web` keeps SPEC §4
   `{token,url,room}` **and** adds `{embed_script, turn_url, run_id}` so the
   Phase 4 SDK has Dograh's real handles. Live-verified.
2. ✅ **WORKFLOW node graph.** `DograhAdapter._agent_to_workflow_definition`
   emits a real `globalNode → startCall → agentNode → endCall` graph
   (`system_prompt` → persona, `first_message` → greeting). **Live-validated**
   against `POST /workflow/{id}/validate` (`is_valid: true`) and confirmed
   runnable (`mode:"webrtc"`).
3. ✅ **Workflow persistence.** The Dograh workflow id is stored on
   `Agent.engine_workflow_id` (created lazily on first session, persisted by the
   sessions router) — one workflow per agent, reused across sessions.
4. ✅ **Transcript polling.** No Dograh push exists, so a background poller
   (`maybe_start_transcript_poller` → `DograhAdapter.poll_transcript`) drains the
   run report during the call and persists + webhooks each item. The
   `/v1/engine/dograh/events` endpoint stays for client-forwarded items.

### Browser test harness — place a real web call

A same-origin consumer page is served by the API itself:

```
GET /webclient      # http://localhost:8000/webclient (or your port)
```
Enter a platform API key + Agent ID → it starts a session through the API,
mounts the Dograh voice widget from `web.embed_script`, and live-polls the
transcript. Click **“Start Voice Call”**, allow the mic, and talk.

### The one thing only a human can do

A real **spoken** conversation needs a browser + microphone (above) — it can't be
automated headless. The transcript-artifact JSON shape is parsed tolerantly
(`TODO(transcript-shape)` in `dograh.py`) and should be eyeballed on the **first
real call**. Indian-language quality (Sarvam STT/TTS) is **Phase 2**, not wired
here. Everything else is built and verified end-to-end against live Dograh.

## Open decisions still owned by you (SPEC §10)

1. Final engine choice (Dograh vs LiveKit) — pending your Hinglish hands-on test.
2. Indian-language provider (Sarvam assumed) — confirm account/keys before Phase 2.
3. Hosting target for first deploy.
4. Webhook config storage — **defaulted** here to columns on `Org`
   (`webhook_url` / `webhook_secret`), with `DEFAULT_WEBHOOK_*` env fallbacks.
