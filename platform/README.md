# VoxVoice — platform (backend)

Provider-agnostic Voice AI Runtime Platform. **The runtime is the product**;
apps (VoxHire, etc.) are *consumers*. Authority: the
[Engineering Constitution](../docs/architecture/engineering-constitution-v2.md)
(Part A architecture, Part B current tech profile) and the
[`.voxvoice/`](../.voxvoice/README.md) governance framework. Read those before
changing anything.

> **Milestone status:** M0 (foundation freeze) and M1 (repository reshape) are
> complete. The engine-adapter axis was removed at M1 (ADR-0001/0007); the
> Runtime Plane is built from M2. **`POST /v1/sessions` returns 503 until M2** —
> a deliberate, documented gap (Constitution migration M1).

## Package layout (Constitution §A14)

```
platform/
├── core/           # dependency floor — contracts, event types, state defs, errors, ids
│   └── providers/  #   capability contracts (STT/TTS/LLM/Transport/Storage/Analytics interfaces)
├── api/            # Control Plane (FastAPI): auth, agents CRUD, sessions, health
├── runtime/        # Runtime Plane (skeleton; built M2+): worker, orchestrator, turn,
│                   #   ai, memory, speech, pipeline (pipeline = the ONLY substrate importer)
├── providers/      # capability plugin implementations (empty; from M2)
├── events/         # event delivery infra: webhook dispatcher now; Event Bus + outbox at M4
├── observability/  # metrics, tracing, cost, logging (skeleton; from M2/M3)
└── tests/          # auth scoping, agents CRUD, session-endpoint contract
```

Import rules (Constitution §A4.1, CI-checkable): `core` imports nothing above it ·
`providers` imports only `core` · `events` never imports `api`/`runtime` ·
`runtime` never imports `api` (only `runtime/pipeline` imports the substrate) ·
`api` reaches the runtime only via a session-control interface + the Event Bus.

## Run (Control Plane)

```bash
cd platform
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # DATABASE_URL, DEFAULT_WEBHOOK_* (optional)

python -m api.bootstrap "Acme Corp" --label dev   # provision org + API key (shown once)
uvicorn api.main:app --reload --port 8000
```

`GET /health` → `{ ok, db }`. Docs at `/docs`.

## What works today (M1)

- Org-scoped API-key auth (`x-api-key`; hashes stored, raw key shown once).
- Agents CRUD (`/v1/agents`), fully org-scoped.
- Session read/end endpoints (`/v1/sessions/{id}`, `/{id}/end`, `/{id}/transcript`).
- Session **start** (`POST /v1/sessions`) validates agent ownership, then returns
  **503** (Runtime Plane arrives at M2).
- Signed webhook fan-out (`session.ended`) via the decoupled `events` module.

## Tests

```bash
cd platform && pytest       # auth scoping, agents CRUD, session-endpoint contract
ruff check core api runtime providers events observability tests
```

Tests run against a throwaway SQLite DB. Capability-contract fakes arrive with the
first providers (M2); runtime/latency/turn-detection tiers follow (TESTING_POLICY).

## History

The Phase-1 Dograh engine-adapter integration was retired at M1 (ADR-0001). Its
live-verified API knowledge is archived in
[`docs/research/dograh-api-notes.md`](../docs/research/dograh-api-notes.md).

The old Dograh deploy runbooks (`DEPLOY.md`, `ORACLE_DEPLOY.md`,
`scripts/oracle_setup.sh`) and the Phase-1 Pipecat prototype (`voicebot/`) were
**removed at M11.18** (2026-10-16). They were kept only until their
Oracle/TLS/WebRTC knowledge had been carried into a current deploy document, and
[`docs/releases/epic11-pre-live-handoff.md`](../docs/releases/epic11-pre-live-handoff.md)
is now that document — it supersedes them outright, and explicitly contradicts
their coturn port list. Both remain in git history.
