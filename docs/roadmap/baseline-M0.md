# VoxVoice — M0 Baseline Snapshot (Foundation Freeze)

| | |
|---|---|
| **Document** | `docs/roadmap/baseline-M0.md` |
| **Status** | Baseline reference — immutable · **Owner:** Technical Owner |
| **Date** | 2026-07-16 |
| **Git tag** | `foundation-v1` |
| **Purpose** | The official engineering baseline. Every future milestone starts from this state. Captures repository structure, dependencies, architecture/governance/documentation posture, statistics, known debt, deferred items, and open risks at the moment version control began. |

## 1. Repository structure (tracked)

```
VoxVoice/
├── .gitignore                 # comprehensive root ignore (secrets, DBs, venvs, toolchains, caches)
├── CLAUDE.md                  # governance pointer (was SPEC duplicate; replaced at M0)
├── .voxvoice/                 # governance system — 14 files (README + 13 policies)
├── docs/
│   ├── INDEX.md · README.md   # documentation constitution + central index
│   ├── SPEC.md                # original spec (superseded in part; banner added M0)
│   ├── architecture/          # engineering-constitution-v2 (RATIFIED) + v1 (superseded)
│   ├── adr/                   # ADR-0001…0007 + P-ADR-0001 (+ README register)
│   ├── audits/                # phase-1, phase-1.5 (immutable)
│   ├── research/              # dograh-api-notes (archived engine knowledge)
│   └── roadmap/               # this baseline
└── platform/                  # Python backend (PROTOTYPE — reshaped from M1 onward)
    ├── api/                   # Control Plane (FastAPI) — KEPT & hardened (ADR-0007)
    ├── adapters/              # engine-adapter axis — DELETED at M1 (ADR-0001/0007)
    ├── webhooks/              # HMAC signing + dispatch — KEPT
    ├── voicebot/              # Pipecat prototype — becomes Runtime Plane seed at M2
    ├── tests/                 # 14 tests (auth scoping, CRUD, session flow, adapter contract)
    ├── scripts/ · webclient/  # oracle setup; browser test harness (webclient replaced at M1)
    └── pyproject.toml
```

**Structure vs. Constitution target (§A14):** the current `platform/` layout (`api, adapters, webhooks, voicebot, tests`) is the **prototype** layout. The target (`core, api, runtime, providers, events, observability, tests`) is created incrementally from **M1** (create `core/`, delete `adapters/`) through M6. This divergence is expected and roadmapped — M0 deliberately does **not** reshape; it freezes the current state under version control so the reshape is tracked and reversible.

## 2. Dependency summary

- **Runtime (Control Plane):** FastAPI, uvicorn, SQLModel, Pydantic v2, pydantic-settings, httpx, python-dotenv. **Dev:** pytest, pytest-asyncio, ruff. (Constitution Part B / P-ADR-0001.)
- **Prototype voicebot (not part of the platform package):** Pipecat ≥ 1.4 + provider SDKs (profile prototype; vendored `.venv`/`tools` are gitignored, not tracked).
- No dependencies added at M0 (non-goal honoured).

## 3. Architecture snapshot

- **Authority:** [Engineering Constitution v2](../architecture/engineering-constitution-v2.md) (RATIFIED) — first-party runtime, two-plane, event-driven, capability-based providers, streaming-first + Turn Intelligence, single System of Record. Decisions recorded as [ADR-0001…0007 + P-ADR-0001](../adr/README.md).
- **Implemented today:** only the Control Plane prototype (auth, agents CRUD, sessions, webhooks) + the retired engine-adapter path + a disconnected voicebot prototype. **No runtime, no events, no live control** yet (see [Phase 1.5 audit](../audits/phase-1.5-voice-runtime-audit.md)).
- **Runtime Maturity at baseline:** 22/100 (Phase 1.5). This is the number M1–M6 move.

## 4. Governance snapshot

Full framework active in [`.voxvoice/`](../../.voxvoice/README.md): authority hierarchy (Constitution → ADRs → Governance → Design → Sprint → Tasks → Code), 13-phase DEVELOPMENT_WORKFLOW, ARCHITECTURE_RULES AR-1…54, 14-dimension REVIEW_CHECKLIST, DEFINITION_OF_DONE, TESTING/PERFORMANCE/SECURITY policies, GIT_WORKFLOW, DECISION_LOG. Binding on every future human/AI task.

## 5. Documentation snapshot

- 36 tracked Markdown documents; central [INDEX.md](../INDEX.md) links all. All relative doc links verified resolving at M0.
- Immutable: audits, accepted ADRs. Living: governance, INDEX, roadmap. Superseded (banners, retained): constitution v1, SPEC.md (in part).

## 6. Repository statistics (at `foundation-v1`)

| Metric | Value |
|---|---|
| Tracked files | 72 |
| Python files (platform) | 28 files, ~1,881 LOC |
| Markdown (docs + governance) | 36 files, ~3,503 LOC |
| Tests | 14, all passing (0.12 s), ruff clean on `api/adapters/webhooks/tests` |
| ADRs | 8 (7 constitutional + 1 profile) |
| Governance documents | 13 (+ index) |
| Secrets in tracked files | 0 (empirically verified via `git ls-files`) |

## 7. Known technical debt (carried into baseline)

Inherited from [Phase 1 audit](../audits/phase-1-repository-audit.md) — to be addressed on the M1–M6 path, not at M0:

- Engine-adapter axis (`adapters/`, `engine_events`, poller, `engine_workflow_id`) — **delete at M1** (ADR-0007).
- Sync DB access in async handlers; per-request `httpx` clients; `last_used_at` write per request — hardened as the Control Plane is reworked.
- Free-string enums; `api⇄webhooks` import cycle; import-time settings binding — addressed under CODING_STANDARDS/ARCHITECTURE_RULES during reshape.
- `voicebot/bot.py` has 2 ruff import-sort findings (prototype; not in the platform package; resolved when promoted to a Runtime Plane plugin at M2).
- Vendored `voicebot/.venv` (700 MB) + `voicebot/tools/` (850 MB) + `platform/.venv` (94 MB) exist on disk, gitignored — physical cleanup optional/deferred (does not affect the repository).

## 8. Deferred decisions (open in DECISION_LOG)

- **DL-0001** — `git init` (**closed at M0** — this baseline).
- **DL-0002** — Rotate exposed LLM/TTS/engine keys (LLM, TTS, engine). **OPEN — owner action.** Keys were on disk in gitignored `.env` files; treated as compromised. Not in version control, but must be rotated in the provider dashboards before any deploy.
- **DL-0004** — ADR extraction (**closed at M0**).
- Provider streaming verification for the target-language STT/TTS (P-ADR-0001) — open, due before M3/M6.

## 9. Open risks

1. **Exposed keys not yet rotated (DL-0002)** — highest open item; repository is clean but the credentials themselves remain valid until the owner rotates them. Owner action, outside M0's power.
2. **Runtime unproven** — no real spoken call has ever completed (Phase 1 H3); the whole runtime is validated theory until M2/M3 on a public-IP host.
3. **Solo-developer scope** — the M1–M6 build is 8–12 focused weeks; mitigated by staged delivery.
4. **Prototype→product promotion discipline** — the voicebot must be rebuilt behind contracts at M2, not copied forward as-is (governance guards this).

## 10. Baseline guarantees

At `foundation-v1`: build imports resolve · 14 tests pass · ruff clean (platform packages) · all doc links resolve · no secrets tracked · both planes' future boundaries documented · every architectural decision recorded as an ADR · governance active. **This is the official state every future milestone starts from.**
