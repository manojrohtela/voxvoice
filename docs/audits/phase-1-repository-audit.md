# VoxVoice — Engineering Audit Report (Phase 1: Complete Repository Audit)

| | |
|---|---|
| **Document** | `docs/audits/phase-1-repository-audit.md` |
| **Status** | Final — v1.0 |
| **Audit date** | 2026-07-16 |
| **Auditor** | Claude Code (acting Chief Software Architect / Code Auditor) |
| **Scope** | Entire repository (`VoxVoice/`), every source file read (~35 files, ~3,100 lines of code and docs) |
| **Verification performed** | Full test suite run (14/14 pass, 0.12 s), `ruff check` run, env files inspected, `demo.db` inspected, no files modified |
| **Supersedes** | — (first audit) |
| **Related** | [`../SPEC.md`](../SPEC.md) (build spec), [`../../platform/README.md`](../../platform/README.md), [`../../platform/DEPLOY.md`](../../platform/DEPLOY.md), [`../../platform/ORACLE_DEPLOY.md`](../../platform/ORACLE_DEPLOY.md) |

---

## 1. Executive Summary

VoxVoice is a Phase 1 "API in front" implementation of a self-hostable voice-agent platform (a Vapi alternative), per the spec in `docs/SPEC.md`. The codebase is **small (~1,600 lines of Python), disciplined, and unusually well-documented for its size** — the adapter boundary is genuinely enforced, all 14 tests pass, org-scoping is tested explicitly, and deviations from the spec (Dograh's real API vs. the assumed one) are honestly recorded rather than papered over.

The three most serious problems are **not in the code's design but in its operational reality**:

1. **The project is not under version control.** There is no `.git` directory anywhere in `VoxVoice/`. The `.gitignore` files are decorative. All work since June 15 exists only as files on one laptop.
2. **Live API keys sit in plaintext on disk** (`platform/.env`: Dograh key; `platform/voicebot/.env`: Groq and Cartesia keys), plus a populated `demo.db`. The moment this folder is committed as-is, they leak.
3. **The end-to-end voice loop has never been closed.** A real spoken call, the actual transcript artifact shape (`TODO(transcript-shape)`), and the Dograh embed widget flow remain unverified — the platform's core product promise is still unproven.

Beyond that, the issues are the expected Phase 1 gaps: sync SQLAlchemy calls inside async handlers, fire-and-forget pollers with an unbounded in-memory de-dupe dict, no DB migrations, no pagination, and a parallel Pipecat "voicebot" track that lives outside the adapter architecture it's supposed to eventually join.

**Verdict: solid foundation, honest engineering, but pre-production.** Fix version control and secrets before writing another line.

---

## 2. Current Architecture Overview

The implemented architecture matches SPEC §1 faithfully:

```
Consumer (webclient / future VoxHire, SDK)
   │  x-api-key
   ▼
FastAPI Platform API  (api/)
   ├─ routers: agents (CRUD), sessions (start/end/get/transcript), engine_events (ingest)
   ├─ security: SHA-256-hashed org-scoped API keys
   ├─ ingest: transcript → persist → webhook fan-out
   ▼  EngineAdapter ABC only
adapters/  (factory selects via ENGINE env)
   ├─ DograhAdapter   — real, live-verified against Dograh v1.35.0
   └─ LiveKitAdapter  — loud stub (NotImplementedError)
   ▼
Dograh (workflow / run / embed-token model) → SQLite (dev) / Postgres (planned)

Separate, unintegrated track:
voicebot/bot.py — Pipecat pipeline (WebRTC → Groq STT → Groq LLM → Cartesia TTS)
```

**Design patterns in use:** Ports-and-adapters (the engine boundary), factory + singleton (`adapters/factory.py`), dependency injection via FastAPI `Depends` (`get_org`, `get_session`), schema/model separation (Pydantic wire contract vs. SQLModel storage), repository-lite via helper functions (`_get_owned_agent`, `_get_owned_session`).

**Dependency flow** is mostly clean: routers → schemas/models/security/adapters. One violation: `webhooks/` (a top-level package) imports `api.config` and `api.models`, while `api.ingest` and `api.routers.sessions` import `webhooks` — a package-level cycle `api ⇄ webhooks` (works only by import-order luck; see §9).

**Runtime data flow (happy path):** `POST /v1/sessions` → resolve org → load agent → `DograhAdapter.start_session` (lazily creates a Dograh workflow, creates a run, mints an embed token) → persist Session + `engine_workflow_id` → emit `session.started` webhook → spawn background transcript poller → return `{token, url, room, embed_script, turn_url, run_id}`.

## 3. Repository Structure Review

```
VoxVoice/
├── CLAUDE.md                 ← byte-identical duplicate of docs/SPEC.md
├── docs/SPEC.md
└── platform/                 ← project root (deliberately not a Python package — good call re stdlib `platform`)
    ├── api/                  ← app, config, db, models, schemas, security, ids, ingest, bootstrap, routers/
    ├── adapters/             ← base ABC, dograh, livekit stub, factory
    ├── webhooks/             ← HMAC signing + dispatch
    ├── webclient/index.html  ← same-origin browser test harness
    ├── tests/                ← 14 tests, FakeAdapter, throwaway SQLite
    ├── scripts/oracle_setup.sh
    ├── voicebot/             ← Pipecat MVP; 850 MB tools/ + 700 MB .venv inside the tree
    ├── .env  ⚠ live Dograh key      demo.db  ⚠ populated DB
    ├── DEPLOY.md / ORACLE_DEPLOY.md / README.md
    └── pyproject.toml
```

Structure matches SPEC §8 except: **`/sdk` (TypeScript) does not exist** and **`/evals` does not exist** (both later-phase, but the spec lists `/evals` under the Phase-1 layout). Naming is consistent and readable throughout. The ~1.5 GB of vendored toolchain/venv inside `voicebot/` is the only structural bloat.

## 4. Runtime Flow Analysis

- **Startup:** `lifespan` → `init_db()` (`create_all`, no migrations) → `register_transcript_ingest(get_adapter())`. Note `api/db.py` calls `get_settings()` **at import time** (`_settings = get_settings()`), so `DATABASE_URL` must be set before any `api.*` import — the tests handle this with careful import ordering in `conftest.py`, which is fragile.
- **Auth per request:** `get_org` hashes the presented key, looks it up, then **writes** `last_used_at` and commits — a DB write on every single request, including reads.
- **Session start:** described above. If the adapter raises `EngineError`, a `status="failed"` session is persisted and a 502 returned. If the adapter raises anything *else* (e.g. a `KeyError` on an unexpected Dograh response shape), the session is never persisted and the client gets a raw 500.
- **Transcripts (two paths):** (a) polling — `maybe_start_transcript_poller` spawns an `asyncio.create_task` loop (5 s interval, 30 min cap) that fetches the Dograh run report, tolerantly parses the artifact, and de-dupes via `DograhAdapter._emitted[engine_ref]` (an in-process counter); (b) push — `/v1/engine/dograh/events` guarded by a shared secret, even though the README admits Dograh has no push webhook — it exists for client-forwarded items.
- **Session end:** platform-side status flip only; Dograh has no cancel-run route (documented). The poller keeps running up to its cap regardless.
- **Webhooks out:** HMAC-SHA256-signed, best-effort, single attempt, errors swallowed and logged. No retry, no delivery record.

## 5. Strengths

- **The adapter boundary is real, not aspirational** — no engine import outside `adapters/`, factory-only selection, contract-tested, and the LiveKit stub fails loudly instead of faking.
- **Live-verified engine integration.** The Dograh mapping (workflow/run/embed-token, node graph, auth header) was exercised against a running v1.35.0 instance, and the *incorrect spec assumptions are documented as deviations* — exemplary honesty (SPEC §9 followed to the letter).
- **Org scoping is enforced and explicitly tested**, including cross-org 404s and the "org B can't start a session on org A's agent" case.
- **Good security hygiene in code:** key hashes only, raw key shown once, HMAC-signed webhooks, engine errors wrapped so raw engine details never reach consumers.
- **Readable, intent-revealing comments** that explain *why* (e.g., why `meta` vs `metadata`, why `platform/` isn't a package), not what.
- **Fast, deterministic test suite** (14 tests, 0.12 s, no live engine needed) with a contract-conforming `FakeAdapter`.
- **Excellent operational docs** — README, DEPLOY.md, ORACLE_DEPLOY.md, and an idempotent `oracle_setup.sh` capture hard-won knowledge (macOS Docker TURN limitation, Oracle dual-firewall gotcha).

## 6. Weaknesses

- No version control, no CI, no migrations, no structured logging, no rate limiting (the last four are acknowledged Phase-4 items, but their absence is still a current-state fact).
- Sync DB access inside async handlers (event-loop blocking under load).
- In-process state (`_emitted` de-dupe, poller tasks, adapter singleton) makes the API **single-process only**; running `uvicorn --workers 2` would silently break transcript de-dupe and polling ownership.
- The voice loop itself (the product) is unproven end-to-end; transcript parsing is speculative (`TODO(transcript-shape)`).
- The `voicebot/` Pipecat track duplicates the "engine" concept outside the adapter architecture.
- Webhook delivery is fire-once with no retry — consumers will silently miss events.

## 7. Dead Code Report

Genuinely little dead *code* — the dead weight is artifacts:

| Item | Kind | Severity | Recommendation (only) |
|---|---|---|---|
| `voicebot/tools/` (850 MB uv cache + bundled CPython) | build artifact in tree | High (repo hygiene) | ignore/relocate; belongs in a global cache |
| `voicebot/.venv` (700 MB), `platform/.venv` (94 MB) | venvs in tree | Low (gitignored) | fine once git exists |
| `platform/demo.db` (populated: 2 orgs, 2 key hashes) | data artifact | Medium | should never ship; `*.db` is gitignored but git doesn't exist |
| `.pytest_cache/`, `.ruff_cache/`, `voxvoice_platform.egg-info/` | caches | Low | gitignored-in-theory |
| `EngineTranscriptEvent` / `/v1/engine/dograh/events` | speculative code path | Low | kept deliberately for "client-forwarded / future push"; acceptable but currently has no real producer |
| `monkeypatch` fixture arg in `test_factory_returns_dograh_by_default` | unused parameter | Trivial | remove |

No unused imports/functions found in `api/`, `adapters/`, `webhooks/` (ruff confirms; the only 2 ruff findings are import-sort issues in `voicebot/bot.py`).

## 8. Duplicate Code Report

| Duplicate | Where | Severity |
|---|---|---|
| **`CLAUDE.md` is byte-identical to `docs/SPEC.md`** (304 lines each) | repo root / docs | Medium — they *will* drift; one should reference the other |
| `_get_owned_agent` / `_get_owned_session` | agents.py:20, sessions.py:32 | Trivial — same "load + org-check + 404" pattern twice; fine at this scale, generalize only if a third resource appears |
| `_h(key)` test helper | defined in 3 test files | Trivial — move to conftest |
| `metadata`↔`meta` translation logic | `AgentRead.from_model`, `SessionRead.from_model`, create/patch handlers | Low — 4 hand-rolled spots for the same rename; a shared helper or Pydantic alias would centralize it |
| Dograh remote-mode `.env` recipe | DEPLOY.md §1, ORACLE_DEPLOY.md §6, oracle_setup.sh | Low — three copies of the same env block; script should be the source of truth |

No duplicate models, services, interfaces, or constants.

## 9. Code Quality Issues

1. **Package-level import cycle `api ⇄ webhooks`** — `webhooks/dispatch.py` imports `api.config`/`api.models`; `api.ingest` imports `webhooks`. *Root cause:* `webhooks` was made a sibling top-level package (per SPEC §8) but depends on `api` internals. *Impact:* fragile import ordering; a future `webhooks`-side import of `api.main` would deadlock. *Recommendation:* invert — pass settings/org data in, or move webhooks under `api/`.
2. **Import-time settings/engine creation in `api/db.py`** — env must be configured before first import; tests work around it with `# noqa: E402` ordering. *Impact:* any new entrypoint that imports `api.*` before setting env silently binds to the wrong DB. *Recommendation:* lazy engine creation.
3. **State mutation as a side-channel:** `DograhAdapter._ensure_workflow` writes `agent.engine_workflow_id` and relies on the *router* remembering to `db.add(agent)` (sessions.py:78). Hidden temporal coupling between adapter and router. *Recommendation:* return the id explicitly or document the contract on the ABC.
4. **Free-string enums** — `channel`, `status`, `role`, `engine` are unvalidated `str`s in both schemas and models. `channel="banana"` reaches the adapter and comes back as a 502 instead of a 422. *Recommendation:* `Literal[...]`/`StrEnum` at the schema layer.
5. **`SessionRead`/`AgentRead` leak nothing today, but `Agent.engine_workflow_id` survival depends on Pydantic's silent extra-field ignoring** in `from_model` — implicit, worth an explicit `exclude`.
6. **Error taxonomy is one class** — every engine failure is `EngineError` → 502, including "you asked for a phone channel" (a client error that should be 400/422, dograh.py:201-204).
7. `test_unknown_engine_raises` manipulates `os.environ` manually with try/finally instead of `monkeypatch` — the idiom exists one test above.
8. Poll interval/timeout (5 s / 1800 s) hardcoded in `ingest.py` defaults rather than `Settings`.

## 10. Performance Risks

| Risk | Location | Severity | Notes |
|---|---|---|---|
| **Sync SQLAlchemy inside async handlers** | all routers, `ingest.transcript_handler` | High (at scale) | `db.commit()` blocks the event loop; under concurrent calls every in-flight request stalls. Recommendation: async engine/session or `run_in_threadpool`. |
| **DB write on every request** | `security.get_org` (last_used_at + commit) | Medium | Doubles round-trips, serializes on the ApiKey row. Recommendation: throttle (update at most once/minute) or background it. |
| **New `httpx.AsyncClient` per call** | `dograh._request`, `poll_transcript`, `webhooks.emit_event` | Medium | No connection pooling; TLS handshake per poll tick × every active session × every 5 s. Recommendation: shared client per adapter. |
| **Unbounded poller fan-out** | `maybe_start_transcript_poller` | Medium | N active sessions ⇒ N tasks × (run fetch + artifact fetch) every 5 s for up to 30 min each, even after the call ends (no linkage from `end_session`). |
| **`_emitted` dict never pruned** | dograh.py:76, 318 | Low-Medium | Slow memory leak, one entry per run, for the process lifetime. |
| **Fire-and-forget `asyncio.create_task` without a reference** | ingest.py:90 | Medium | Python docs explicitly warn the task may be garbage-collected mid-flight; also no cancellation on shutdown. Keep a task set. |
| Webhook awaited inline in request path | sessions.py:82,115 | Low | Up to 10 s added to `POST /sessions` latency if the consumer endpoint is slow (errors are swallowed but the await still blocks the response). |

## 11. Security Risks

| # | Risk | Severity | Detail |
|---|---|---|---|
| S1 | **Live secrets on disk in plaintext** | **Critical (process)** | `platform/.env` → real Dograh key; `platform/voicebot/.env` → real **Groq** and **Cartesia** keys (README even says "already populated with your keys"). With no git, `.gitignore` protects nothing; a naive `git init && git add .` publishes them. **Rotate Groq/Cartesia keys and treat all three as compromised-by-default.** |
| S2 | Weak default `ENGINE_CALLBACK_SECRET` (`change-me…`) baked into `config.py` | High | If unset in prod, the transcript-ingest endpoint is guessable → transcript forgery into any session. Fail startup if unset in non-dev. |
| S3 | Non-constant-time secret compare | Low-Medium | `engine_events.py:29` uses `!=`; use `hmac.compare_digest`. |
| S4 | XSS in webclient transcript rendering | Medium (dev-only page) | `index.html:107-108` injects `t.text` via `innerHTML` — transcript text is user-spoken/engine-derived. Escape it. The page is served by the prod app, unauthenticated. |
| S5 | Unsalted SHA-256 API-key hashes | Low | Acceptable for 256-bit random keys (not brute-forceable), but a keyed hash (HMAC with a server pepper) is cheap defense-in-depth. |
| S6 | No rate limiting / brute-force guard on `x-api-key` | Medium | Acknowledged Phase 4; note it also applies to the engine-events secret. |
| S7 | Webhook SSRF surface | Low | Org `webhook_url` is operator-set today (bootstrap CLI), but once orgs self-serve it, the platform will POST to arbitrary URLs incl. internal addresses. Validate/deny-list before that ships. |
| S8 | `demo.db` with real org/key hashes in the tree | Medium | Same class as S1. |
| S9 | Error detail passthrough is safe (messages are our own strings), and no secrets are logged — **good**. Token values do appear in `sessionOut` JSON on the webclient page; fine for a harness. | — | |

Auth/authz design itself (org-scoped keys, 404-for-other-org) is correct and tested.

## 12. Scalability Assessment

- **Multiple engines:** ✅ genuinely ready — the one axis the architecture was built around.
- **Multiple STT/TTS/LLM providers:** 🟡 fields exist (`stt_provider`, `tts_provider`, `llm_model`) but nothing consumes them; DograhAdapter's node graph ignores them (documented as Phase 2 `workflow_configurations` work). The *shape* is right.
- **Multiple voice agents per org:** ✅ trivially supported.
- **Plugins / tool calling:** 🟡 `tools` JSON column and `tool.called` event name reserved; no execution path. No obstacle foreseen.
- **Horizontal scaling:** 🔴 blocked by in-process state — adapter singleton with `_emitted`, in-process poller tasks, SQLite. Postgres migration is planned; the poller/de-dupe state must move to the DB (e.g., store last-emitted count on Session) before >1 worker is possible. This is the biggest architectural gap not named in the docs.
- **Future AI capabilities (KB/RAG, squads):** neutral — nothing helps, nothing blocks.

## 13. Missing Components

Against the spec and against a production voice platform:

1. **Version control + CI** (not in spec explicitly, but §9 says "commit in small units" — there is nothing to commit *to*).
2. **`/sdk` TypeScript package** (SPEC §6/§8) — consumers currently must hand-mount `embed_script`.
3. **`/evals` Hinglish eval harness** (listed in the §8 Phase-1 layout; content is Phase 2).
4. **DB migrations** (Alembic) — `create_all` cannot evolve a Postgres schema.
5. **Webhook retry/dead-letter + delivery log.**
6. **Voice runtime pieces owned by the platform:** none — turn detection, barge-in, endpointing all delegated to Dograh; `min/max_endpointing_delay` and `allow_interruptions` are stored but only `allow_interruptions` is mapped into the workflow. `language`, `tts_voice`, `llm_model`, `stt_provider`, `tts_provider`, `min/max_endpointing_delay` are stored-but-unapplied (documented, but consumers can set them today and silently get defaults — worth a 501/warning).
7. **Session lifecycle truth:** nothing marks a session `ended` when the *caller* hangs up (poller learns `is_completed` but doesn't update Session status).
8. **Rate limiting, structured logging, request IDs, metrics** (Phase 4 by design).
9. **Pagination** on `GET /v1/agents` and no `GET /v1/sessions` list at all (spec §4 omits the list too, but consumers will need it).
10. **Cascade/behavior on agent delete** with existing sessions (FK violation on Postgres; silent orphans on SQLite).

## 14. Technical Debt

Ordered by carrying cost:

1. No-git/no-CI — compounds daily.
2. Plaintext secrets — one bad command from an incident.
3. In-process poller + de-dupe state — must be redesigned before multi-worker/Postgres; the longer it stays the more code leans on it.
4. Sync-DB-in-async — a rewrite touch on every router later.
5. Unapplied Agent config fields — a growing silent-lie surface as consumers adopt the API.
6. `CLAUDE.md`/`SPEC.md` duplication — doc drift.
7. Speculative transcript parser — unverified assumption at the heart of the product.
8. Voicebot as an out-of-architecture second engine.
9. Doc/env-recipe triplication.

## 15. High Priority Issues

| ID | Issue | Severity | Root cause | Impact | Recommendation |
|---|---|---|---|---|---|
| H1 | No version control | Critical | Project never `git init`-ed | Total-loss risk; no history/review/rollback; SPEC §9 unfulfillable | `git init` + first commit **after** H2 |
| H2 | Live keys in `.env`s + `demo.db` in tree | Critical | Convenience during verification | Key leak on first commit/share; Groq/Cartesia are paid, internet-usable keys | Rotate all three; keep `.env`s out; verify `.gitignore` takes effect before first commit |
| H3 | Voice loop never closed end-to-end | High | macOS Docker TURN limitation (documented) | Product's core claim unverified; `_parse_transcript` may not match reality | Do the Oracle deploy (runbook exists), make one real call, confirm `TODO(transcript-shape)` |
| H4 | Multi-worker unsafety (adapter singleton, `_emitted`, in-process pollers) | High | Phase-1 single-process assumptions, undocumented | Silent transcript duplication/loss the day `--workers 2` or a second host appears | Document the constraint now; move de-dupe offset onto `Session` when Postgres lands |
| H5 | Non-`EngineError` exceptions in `start_session` → unpersisted session + raw 500 | High | Only `EngineError` caught (sessions.py:61-70) | Unexpected Dograh response shape (`KeyError`) loses the session record and leaks a stack-trace-shaped 500 | Broaden except; persist `failed` session in a finally-style path |

## 16. Medium Priority Issues

| ID | Issue | Severity | Root cause | Impact | Recommendation |
|---|---|---|---|---|---|
| M1 | Sync DB in async handlers | Medium (High at scale) | SQLModel sync session simplicity | Event-loop stalls under concurrency | Async engine or threadpool when moving to Postgres |
| M2 | `asyncio.create_task` unreferenced (ingest.py:90) | Medium | Fire-and-forget pattern | Poller can be GC'd mid-call → missing transcripts, no shutdown cancellation | Hold tasks in a set; cancel in lifespan teardown |
| M3 | Weak default engine-callback secret + `!=` compare | Medium | Dev default in code | Transcript forgery if unset in prod | Startup guard + `hmac.compare_digest` (S2/S3) |
| M4 | Agent delete leaves orphaned sessions / FK violation on Postgres | Medium | No cascade decision | Prod 500s on delete of any used agent | Decide: restrict, soft-delete, or cascade |
| M5 | DB write per request in `get_org` | Medium | Eager last_used_at | Write amplification, hot-row contention | Throttle updates |
| M6 | Per-call `httpx.AsyncClient` | Medium | Simplicity | Latency + socket churn on 5 s polls | One pooled client per adapter |
| M7 | Stored-but-unapplied agent fields (providers, voice, endpointing) | Medium | Phase 2 deferral, silent | Consumers configure things that do nothing | Reject or warn until wired |
| M8 | Webclient `innerHTML` XSS (S4) | Medium | Unescaped interpolation | Script injection via spoken/forged transcript on an app-served page | Escape text nodes |
| M9 | Session never marked `ended` on caller hang-up | Medium | No `is_completed` → status linkage | Sessions stuck `active`; wrong analytics/billing later | Poller updates Session on completion |
| M10 | `webclient/index.html`: no fetch error handling, no session end on page close | Low-Med | Test-harness scope | Dangling sessions during demos | Acceptable for a harness; note it |

## 17. Low Priority Issues

- L1: `CLAUDE.md` ≡ `docs/SPEC.md` — replace one with a pointer.
- L2: Free-string enums (§9.4) → `Literal`s.
- L3: `api ⇄ webhooks` package cycle (§9.1).
- L4: Import-time settings binding in `db.py` (§9.2).
- L5: Count-offset transcript de-dupe assumes the artifact only ever appends — if Dograh rewrites/reorders, items dupe or vanish; also duplicates after process restart mid-call.
- L6: `ts=0.0` fallback in `_parse_transcript` + `ORDER BY ts` can scramble ordering of items lacking timestamps; add insertion order as tiebreaker.
- L7: No pagination; no sessions-list endpoint.
- L8: `Session` lacks `created_at` (only `started_at`, null for failed sessions).
- L9: Poll cadence hardcoded, not in `Settings`.
- L10: 2 ruff findings in `voicebot/bot.py` (import sort) — the "ruff clean" claim (SPEC §9) holds for the platform, not the voicebot.
- L11: Test gaps: webhook emission (signature, envelope), poll loop, `_parse_transcript` variants, bootstrap CLI, `PATCH` of unknown fields, health endpoint.
- L12: `voicebot/tools/` 850 MB artifact in tree.
- L13: `test_unknown_engine_raises` env juggling vs `monkeypatch` idiom.

## 18. Refactoring Opportunities (Recommendations Only)

1. **Lifecycle-owned infrastructure:** move poller task-set, shared `httpx` client, and adapter into FastAPI lifespan-managed state; gives shutdown cancellation and pooling in one move.
2. **Persist transcript cursor on `Session`** (e.g. `transcript_emitted_count`) — kills `_emitted`, survives restarts, enables multi-worker.
3. **Typed enums + a `PlatformError` hierarchy** (`EngineError`, `UnsupportedChannel`, `EngineUnavailable`) mapped centrally to status codes via an exception handler.
4. **`api/webhooks.py` or dependency-inverted webhooks package** to break the cycle.
5. **Alembic from day one of the Postgres migration**, not after.
6. **`PipecatAdapter`**: fold `voicebot/` behind the existing ABC (the voicebot README already names this as the plan) — it would also finally give the platform a first-party engine where turn-detection/interruption parameters are actually honoured.
7. **Metadata alias handling** via a single helper or Pydantic `alias="metadata"` config instead of four hand-rolled translations.
8. Extract the thrice-duplicated Dograh remote `.env` recipe so `oracle_setup.sh` is the single source of truth and the two docs reference it.

## 19–23. Scores

| Dimension | Score | Rationale |
|---|---|---|
| **19. Overall Codebase Health** | **72/100** | Clean, tested, honest code (+); no VCS, secrets on disk, unclosed voice loop (−) |
| **20. Architecture** | **84/100** | The adapter boundary is exemplary and enforced; deductions for api⇄webhooks cycle, in-process state, import-time config |
| **21. Maintainability** | **78/100** | Small, superbly commented, consistently named, fast tests; deductions for doc duplication, no migrations, test gaps, hidden adapter↔router coupling |
| **22. Scalability** | **58/100** | Engine-swap axis: excellent. Everything else single-process by construction: sync DB, singleton state, per-session pollers, SQLite, no pagination |
| **23. Voice Runtime Maturity** | **34/100** | All voice intelligence is rented from Dograh; endpointing/provider config stored but inert; transcripts poll-based with speculative parsing; **no completed real spoken call**; the Pipecat MVP proves a pipeline but sits outside the platform (and its own README notes VAD never fired locally) |

---

## Recommended first actions (pending owner approval — nothing implemented)

1. Rotate the Groq/Cartesia/Dograh keys, then `git init` + first commit with `.gitignore` verified (H1/H2).
2. Execute the existing Oracle runbook to close one real spoken call and pin down the transcript artifact shape (H3).
3. Fix the two silent-failure hazards: unreferenced poller tasks (M2) and non-`EngineError` handling in session start (H5).

*End of Phase 1 audit. No files were modified during the audit itself; this document and the documentation index were added afterwards under the project's documentation mandate.*
