# Voice Agent Platform — Build Specification

> ⚠️ **SUPERSEDED IN PART (M0, 2026-07-16).** This document is the original build
> spec. It remains authoritative for **product goal, consumer model, org/auth
> contract, and the Hinglish acceptance phrases**, but is superseded by the
> [Engineering Constitution v2](architecture/engineering-constitution-v2.md) on:
> the engine-adapter architecture (§5 → capability-based provider architecture),
> the Dograh-primary decision (§10.1 → first-party runtime, ADR-0001), and the
> repo layout (§8 → Constitution §A14). See Constitution §22 and `docs/adr/`.
> Retained unmodified below except this banner.

> **For:** Claude Code
> **Owner:** HeyAgentHive
> **Goal:** Build a self-hostable, context-driven voice-agent platform (a Vapi alternative). The voice agent is the product; other apps (VoxHire interviews, a future sales tool) are *consumers* that call it.

---

## 0. How to use this document

This is an executable build spec, not a design essay. Build in the phase order given. Each phase has **deliverables** and **acceptance criteria** — do not move to the next phase until the current phase's acceptance criteria pass. Where a decision is open, the spec says so explicitly; ask the owner rather than guessing.

**Prime directive:** *Own the interface, rent the engine.* We expose our own API/SDK. The underlying orchestration engine (Dograh, or LiveKit as fallback) sits behind an adapter and must be swappable. No consumer code may call the engine directly.

---

## 1. Architecture

```
Consumer apps (VoxHire, sales tool, web/mobile)
        │  call OUR API/SDK only
        ▼
┌─────────────────────────────────────────────┐
│  Platform API  (FastAPI, Python)             │
│   - agents:    create / read / update / del  │
│   - sessions:  start a call, return token    │
│   - events:    webhooks out to consumers     │
│   - auth:      org-scoped API keys           │
└─────────────────────────────────────────────┘
        │  via ENGINE ADAPTER (interface)
        ▼
┌─────────────────────────────────────────────┐
│  Engine Adapter (abstract)                   │
│   DograhAdapter  (primary)                   │
│   LiveKitAdapter (fallback, stub for now)    │
└─────────────────────────────────────────────┘
        ▼
   Orchestration engine (STT → LLM → TTS,
   turn detection, barge-in, telephony)
        ▼
   Supabase (Postgres): agents, sessions, transcripts, orgs, api_keys
```

### Non-negotiable boundaries
- Consumers talk to the Platform API. Never to the engine.
- All engine interaction goes through `EngineAdapter`. Swapping engines must touch only the adapter, never the API or consumers.
- Config and data persist in Supabase (Postgres). Local dev may use SQLite via the same SQLModel models.

---

## 2. Tech stack (fixed)

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI + Pydantic v2 | Python 3.11+ |
| ORM / models | SQLModel | SQLite locally → Supabase Postgres in prod (no model changes) |
| DB | Supabase (Postgres) | Reuse existing HeyAgentHive Supabase pattern |
| Engine (primary) | Dograh (self-hosted, Docker) | Behind `DograhAdapter` |
| Engine (fallback) | LiveKit Agents | `LiveKitAdapter` — interface-compatible stub only for now |
| Indian-lang models | Sarvam AI (STT/TTS) | Pluggable provider; the moat |
| Auth | Org-scoped API keys (header `x-api-key`) | JWT internally; reuse VoxHire pattern |
| Frontend SDK | TypeScript, framework-agnostic core + thin React wrapper | Wraps engine web-call client |

**Secrets:** never hardcode. Read from env. Provide `.env.example` only. Do not commit `.env`. (Note: prior project credentials were exposed in chat history — assume all such keys are rotated; never reference old values.)

---

## 3. Data model

Implement as SQLModel tables.

```
Org
  id (org_xxx, pk)
  name
  created_at

ApiKey
  id (key_xxx, pk)
  org_id (fk Org)
  key_hash            # store a hash, never the raw key
  label
  created_at
  last_used_at

Agent
  id (agt_xxx, pk)
  org_id (fk Org)
  name
  system_prompt
  first_message
  language            # e.g. "hi-IN", "en-IN", "hinglish"
  stt_provider        # "dograh_default" | "sarvam" | ...
  tts_provider        # "dograh_default" | "sarvam" | "cartesia" | ...
  tts_voice
  llm_model
  allow_interruptions (bool, default true)
  min_endpointing_delay (float, default 0.5)
  max_endpointing_delay (float, default 6.0)
  tools (JSON)        # list of tool definitions; [] for now
  metadata (JSON)     # free-form, consumer-defined context
  created_at, updated_at

Session
  id (ses_xxx, pk)
  org_id (fk Org)
  agent_id (fk Agent)
  engine            # "dograh" | "livekit"
  engine_ref        # the engine's own room/call id
  channel           # "web" | "phone_in" | "phone_out"
  status            # "created" | "active" | "ended" | "failed"
  started_at, ended_at
  metadata (JSON)   # per-session context passed by the consumer

Transcript
  id (ts_xxx, pk)
  session_id (fk Session)
  role              # "user" | "agent"
  text
  ts (float)
```

---

## 4. Platform API contract

All endpoints require `x-api-key`. Resolve to an Org; scope every query to that org. Return 401 if missing/invalid.

### Agents
```
POST   /v1/agents
  body:  { name, system_prompt, first_message?, language?, stt_provider?,
           tts_provider?, tts_voice?, llm_model?, allow_interruptions?,
           min_endpointing_delay?, max_endpointing_delay?, tools?, metadata? }
  201 -> Agent

GET    /v1/agents            -> [Agent]   (org-scoped)
GET    /v1/agents/{id}        -> Agent     (404 if not in org)
PATCH  /v1/agents/{id}        -> Agent     (partial update)
DELETE /v1/agents/{id}        -> { deleted: id }
```

### Sessions
```
POST   /v1/sessions
  body:  { agent_id, channel?="web", metadata? }
  201 -> { session_id, engine, channel,
           web?: { token, url, room },          # present when channel="web"
           phone?: { call_id }                  # present for phone channels
         }
  Behaviour: load agent (org-scoped) -> call EngineAdapter.start_session(agent, session)
             -> persist Session -> return engine connection details.

POST   /v1/sessions/{id}/end -> { session_id, status:"ended" }
GET    /v1/sessions/{id}      -> Session
GET    /v1/sessions/{id}/transcript -> [Transcript]
```

### Webhooks (events out to consumers)
```
Org can register a webhook URL (config row or env for now).
Emit POST to that URL on:
  session.started, session.ended,
  transcript.item   ({ session_id, role, text, ts }),
  tool.called       (later)
Payload signed with the org's secret (HMAC SHA-256 in `x-signature`).
```

### Health
```
GET /health -> { ok, engine, db }
```

---

## 5. Engine adapter interface

Define an abstract base. Implement `DograhAdapter` fully; `LiveKitAdapter` as a stub raising `NotImplementedError` with TODOs.

```python
class EngineAdapter(ABC):
    name: str

    @abstractmethod
    async def start_session(self, agent: Agent, session: Session) -> EngineSession:
        """Create a call/room in the engine for this agent's config.
        Returns connection details (web token+url+room, or phone call id)."""

    @abstractmethod
    async def end_session(self, session: Session) -> None: ...

    @abstractmethod
    async def on_transcript(self, handler: Callable) -> None:
        """Register a callback the engine calls with transcript items;
        the platform persists them and fans out via webhooks."""

    # Optional now, required for parity later:
    async def start_outbound_call(self, agent, to_number, session): ...
    async def register_inbound_number(self, number, agent): ...
```

`EngineSession` dataclass: `{ engine_ref, web: {token,url,room}|None, phone: {call_id}|None }`.

### DograhAdapter requirements
- Talk to the self-hosted Dograh instance (Docker, `localhost:3010` in dev) via its REST API / SDK (`dograh-sdk` on PyPI). Confirm the exact endpoints from Dograh docs at build time; do not assume.
- Map our `Agent` config → Dograh's agent/workflow config. Where Dograh lacks a field, store our value in `metadata` and apply what Dograh supports.
- Map our `stt_provider`/`tts_provider` → Dograh's BYOK provider settings. Must support pointing STT/TTS at **Sarvam** for Hinglish.
- Subscribe to Dograh's call/transcript events → feed `on_transcript` → persist + webhook.
- **If Dograh cannot do something via API (only via its visual builder), document it clearly as a limitation** and surface a TODO; do not silently fake it.

---

## 6. Frontend SDK

A small TypeScript package consumers embed. It must hide the engine entirely.

```ts
const vf = new VoiceForge({ apiKey, baseUrl });
const { sessionId, web } = await vf.startSession({ agentId, metadata });
const call = vf.connectWeb(web);   // wraps the engine's web client (Dograh's)
call.on("state", s => ...);        // "listening" | "thinking" | "speaking"
call.on("transcript", t => ...);
call.mute(); call.end();
```

- Core is framework-agnostic; ship a thin React hook `useVoiceForge()` on top.
- Mic permission handled inside `connectWeb`. Localhost works without HTTPS.
- The consumer never imports anything from Dograh/LiveKit.

---

## 7. Phased build plan

### Phase 1 — API in front (foundation)
**Deliverables:** FastAPI app; SQLModel models; org + api-key auth; full agents CRUD; sessions endpoints; `EngineAdapter` ABC + `DograhAdapter` (web sessions working); transcript persistence + webhook fan-out; `.env.example`; README run steps; `LiveKitAdapter` stub.
**Acceptance:**
- A consumer with an API key can create an agent, start a **web** session, complete a spoken conversation through Dograh, and read the transcript via `GET /sessions/{id}/transcript`.
- All agent/session queries are correctly org-scoped (cross-org access returns 404/401).
- Swapping the adapter is config-driven (`ENGINE=dograh|livekit`); no API/consumer code references the engine.

### Phase 2 — The Hinglish moat
**Deliverables:** Sarvam (or chosen Indian-lang) STT+TTS wired as selectable providers via the adapter; `language` field honoured; Hinglish backchannels/pronunciation tuning hooks; an eval script that replays a fixed set of Hinglish phrases and logs STT output for manual scoring.
**Acceptance:**
- An agent with `language="hinglish"`, `stt_provider="sarvam"`, `tts_provider="sarvam"` transcribes mixed Hindi-English test phrases noticeably better than the Dograh default, demonstrated by the eval script output.
- Test phrases (must be in the eval set):
  - "Mujhe apna interview reschedule karna hai, kya aap available ho kal?"
  - "Actually mera budget thoda tight hai, koi discount mil sakta hai?"
  - "Teen baje, pachaas hazaar, DLF phase 3"

### Phase 3 — Feature parity (Vapi-level)
Build, each behind the API, each with its own acceptance test:
- **Knowledge base:** upload docs → vector store → retrieval injected into agent context.
- **Tools / function calling:** agent calls consumer-defined functions mid-call; emit `tool.called` webhook.
- **Squads / multi-agent handoff:** route between agents in one session, preserving context (use Dograh's if solid; else implement in adapter).
- **Telephony:** `start_outbound_call`, inbound number registration (Twilio/Telnyx via Dograh).
- **Analytics:** call volume, duration, outcome per org.
- **Usage metering:** per-minute session metering for billing.

### Phase 4 — Consumers & hardening
- Web + mobile consumer SDKs finalised.
- Wire **VoxHire** as consumer #1: it passes interview context via `metadata`/`system_prompt`; all interview logic stays in VoxHire, not the platform.
- Self-host on Oracle Cloud (mind UDP/TURN for WebRTC). Migrate DB from SQLite to Supabase.
- Rate limiting, structured logging, error handling, retries on engine calls.

---

## 8. Repo layout

```
/platform
  /api            FastAPI app (routers, auth, models, schemas)
  /adapters       engine_adapter.py, dograh_adapter.py, livekit_adapter.py
  /webhooks       signing + dispatch
  /evals          hinglish_eval.py + phrase set
  /tests          pytest: auth scoping, CRUD, session flow, adapter contract
  .env.example
  README.md
/sdk
  /core           TypeScript engine-agnostic client
  /react          useVoiceForge hook
/docs
  SPEC.md         (this file)
```

---

## 9. Coding standards & guardrails

- Python: type hints everywhere, Pydantic v2, async FastAPI handlers, `ruff` clean.
- Tests with pytest; every endpoint and the adapter contract must have tests. Org-scoping must be tested explicitly (a key from org A must not see org B's agents).
- No secrets in code or logs. Redact tokens in logs.
- Every engine call wrapped in try/except with a typed platform error; never leak raw engine errors to consumers.
- Commit in small, reviewable units with clear messages.
- When Dograh's real API differs from assumptions here, **follow Dograh's actual API and note the deviation in code comments + README**, keeping our API contract (Section 4) stable for consumers.

---

## 10. Open decisions (ask the owner — do not guess)

1. **Engine final choice.** Spec assumes Dograh. If the owner's Hinglish hands-on test fails, switch primary to LiveKit (`LiveKitAdapter` becomes the real one). Confirm before Phase 2.
2. **Indian-language provider.** Sarvam assumed for STT/TTS. Confirm account/keys, or substitute.
3. **Hosting target** for first deploy (Oracle Cloud vs Dograh cloud vs other).
4. **Webhook config** storage (DB table vs per-org settings) — minor; default to a DB column on Org.
```
