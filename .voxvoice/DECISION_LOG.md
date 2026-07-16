# DECISION_LOG.md — Engineering Decision Log

**Status:** Active, append-only (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 (records decisions *below* ADR level; anything architectural belongs in `docs/adr/`, not here — when in doubt, it's an ADR).

**What belongs here:** dependency additions · accepted technical debt (with revisit date) · deferred work backing `DEFERRED(DL-xxx)` markers · accepted performance/quality trade-offs · process/governance changes · notable non-architectural choices (tool selection, test strategy calls, hotfix compressions).

**Entry format (append at top of the table):**

| ID | Date | Owner | Summary | Rationale | Related ADR/doc | Impact | Status |
|---|---|---|---|---|---|---|---|
| DL-0005 | 2026-07-16 | Technical Owner | M1 reshape: deleted engine-adapter axis; `POST /v1/sessions` now returns 503 until the Runtime Plane (M2) | Constitution migration M1 + ADR-0001/0007 mandate deleting `adapters/`, which removes the only session-start path; 503 is the honest interim per the Constitution ("temporary 503, honest gap"). Adapter-flow tests replaced with 503/scoping tests. `webhooks/`→`events/` and decoupled from `api` to break the audit's import cycle (AR-15) | [migration-progress](../docs/roadmap/migration-progress.md), [ADR-0007](../docs/adr/ADR-0007-keep-control-plane-delete-adapters.md) | Session start unavailable until M2; all other endpoints unchanged; behaviour change is documented, not silent | Accepted |
| DL-0004 | 2026-07-16 | Technical Owner | Constitution v2 ratified; ADR-0001…0007 + P-ADR-0001 extraction deferred to migration M0 | Governance bootstrap ordered with constitution treated as approved; ADR files are an M0 deliverable alongside git init so they land in the first commit | [Constitution v2 §3](../docs/architecture/engineering-constitution-v2.md), R5 | ADRs pending; constitution binding meanwhile | **Closed 2026-07-16 (M0): ADR-0001…0007 + P-ADR-0001 created in [docs/adr/](../docs/adr/README.md)** |
| DL-0003 | 2026-07-16 | Technical Owner | Governance system v1 established in `.voxvoice/` (13 documents) | Mandatory framework for all future human/AI implementation, built around the ratified Constitution | [.voxvoice/README.md](README.md) | All future tasks bound by DEVELOPMENT_WORKFLOW + DoD | Accepted |
| DL-0002 | 2026-07-16 | Technical Owner | Exposed credentials (LLM/TTS/engine keys in `platform/.env`, `platform/voicebot/.env`) treated as compromised; rotation required at M0 before first commit | Phase-1 audit H2/S1; SECURITY_POLICY §1 rotate-first rule | [Phase-1 audit §11](../docs/audits/phase-1-repository-audit.md) | Blocks `git init`; blocks any deploy | Open — due at M0 |
| DL-0001 | 2026-07-16 | Technical Owner | Repository has no version control; `git init` + verified `.gitignore` is the first engineering action (M0) | Phase-1 audit H1; GIT_WORKFLOW is inoperative until then | [Phase-1 audit §15](../docs/audits/phase-1-repository-audit.md) | All GIT_WORKFLOW rules dormant until executed | Open — due at M0 |

**Rules:** IDs are sequential and never reused · entries are never edited after acceptance — status updates append a note (`Status: Closed 2026-08-01, see DL-0009`) · every `DEFERRED(DL-xxx)` in code must resolve to an entry here with an Open status and an owner · Open entries are reviewed at every release (GOVERNANCE §10).
