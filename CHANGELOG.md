# Changelog

All notable releases of the VoxVoice platform. Versioning follows SemVer at the
platform-baseline level (see `docs/releases/`).

## Unreleased — Epic 12 (Developer Platform) · 🟢 M12.1 Foundation & architecture

### Added

- **M12.1 — Developer Platform foundation** ([ADR-0055](docs/adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md),
  DL-0235…DL-0237). Epic 12 productizes the M11 runtime rather than rebuilding it.
  - `api.console.app:build_console_app` — one local product application that **wraps**
    `build_webrtc_app` unchanged and adds `/v1/console/*`. The developer API (`/v1/agents`,
    `/v1/sessions`, `/v1/webrtc/*`) and `/health` are served exactly as before.
  - `GET /v1/console/meta` — API version, console API revision, runtime description, and the
    capabilities implemented now (`runtime.status`). Unauthenticated and secret-free (tested
    with planted credentials).
  - Console error envelope `{"error": {"type","message","request_id"}}`, scoped to console errors
    so M11's `{"detail": …}` bodies cannot change. `x-request-id` is echoed only when safe.
  - Optional one-process local product mode: `VOXVOICE_CONSOLE_DIST` serves the built console at
    `/console/`, with SPA fallback and path-traversal refusal. A misconfigured path is a startup
    error.
  - `console/` — the React + TypeScript + Vite console: app shell, capability-driven navigation,
    Overview with **real** runtime status, and an honest "not yet available" surface for every
    later milestone. 17 tests. Exact-pinned dependencies (DL-0236).
  - CI job `console` (typecheck, test, build, `npm audit --audit-level=high`).
  - Epic 12 documentation: product vision, HLD, LLD, DB design, API design, frontend architecture,
    local development, security model and implementation plan.
- **No existing behaviour changed.** No pre-`v4.0.0` `platform/api/` file modified (`E10-7`), no
  Epic 10/11 module touched, no migration, no table, no event type, no Python dependency.

- **M12.2 — Sign-in & organizations** (DL-0239). Real local sign-in for the console.
  - `dp_user`, `dp_membership`, `dp_console_session` (migration `0037`, a new chain after the
    events chain; real FKs to the M11 `org`). Passwords are stored as stdlib scrypt hashes and
    session tokens as SHA-256; nothing is stored in plaintext.
  - `/v1/console/auth/{signup,login,logout}`, `/v1/console/me`, `/v1/console/organizations`,
    `/v1/console/session/organization`, `/v1/console/organization` (+ `/members`). The session is
    an `HttpOnly; SameSite=Strict` cookie, `Secure` outside `APP_ENV=dev`, with absolute and idle
    expiry and server-side revocation. Mutations require `x-voxvoice-console: 1` (CSRF). Sign-in
    is throttled. The role matrix is enforced server-side.
  - Sign-up policy `VOXVOICE_CONSOLE_SIGNUP=first_run|open|closed` (default `first_run`: the first
    local account only). `python -m api.console.bootstrap_owner` attaches an owner to an M11 org
    or creates an account while sign-up is closed. Nothing creates a user at startup.
  - Console: sign-in, first-run sign-up, organization onboarding, organization switcher,
    sign-out, and Settings (account, organization with owner-only rename, members, new
    organization). No browser storage is used. 29 console tests.
  - `/v1/console/meta` is at revision `m12.2`, with capability `identity` and a `signup` block.
    Validation errors on `/v1/console/*` use the console envelope. M11 bodies are unchanged.
  - No new dependency (Python or npm). No pre-`v4.0.0` `api/` file modified. The developer API
    and the M11 runtime are unchanged.

- **M12.3 — Agents** (DL-0240). Real agent management in the console over the **one** M11
  `agent` row.
  - `/v1/console/agents` list/create, `/{id}` read/edit, `/{id}/archive`, and `/options`. The
    organization comes from the session, cross-org access returns 404, the role matrix applies
    (viewer reads, developer writes), and writes need the CSRF header.
  - Only fields that reach the runtime are editable (name, instructions, greeting, language,
    model, voice). A test drives the frozen `create_session` to prove each one arrives in
    `StartSessionRequest`. `allow_interruptions`/endpointing/tools are neither shown nor written
    (F-1 stays open for an owner decision).
  - Languages come from what the STT plugin declares (`bootstrap.declared_stt_languages`,
    additive; F-4). Undeclared languages are refused, and existing agents that have one are flagged.
  - Archive replaces delete (`dp_agent_state`, migration `0038`, `ON DELETE CASCADE`; F-5).
  - Console: Agents list, editor (create/edit/archive, read-only for viewers, only changed
    fields sent). `/v1/console/meta` is at revision `m12.3`, with capability `agents`.
  - No dependency added. No pre-`v4.0.0` `api/` file modified. The developer API and runtime are
    unchanged.

- **M12.4 — Real Playground** (DL-0241). Talk to an agent from the console over the one M11
  voice path.
  - `/v1/console/playground/{rendezvous, sessions, sessions/{id}/end}` call the frozen
    `RendezvousRegistry.reserve`, `create_webrtc_session` and `end_session`. The browser answers the
    offer on the existing `/v1/webrtc/rendezvous/*` with only the single-purpose token. There is no
    second WebRTC path, signalling channel, session API or runtime.
  - Server-side checks: signed-in, developer role, CSRF header, agent in the session's
    organization, not archived, language declared. Foreign rendezvous and sessions are refused.
    Sessions are tagged `source: console_playground`, `started_by`.
  - `console/src/voice/call.ts` (`VoiceCall`): a framework-free port of the proven `client.html`
    flow, failing fast on a refused start and never leaking a late session. Also the Playground
    page (agent picker, start/hang up/mute, timer, connection log, "voice not enabled"
    explanation) and "Talk to this agent" from the agent editor.
  - A real WebRTC call (a genuine aiortc peer, real SDP/ICE/DTLS/RTP) runs through the console
    routes in the test suite. Console API revision `m12.4`, capability `playground`.
  - No dependency added. No pre-`v4.0.0` `api/` file modified. The M11 runtime and developer API are
    unchanged.

- **M12.5 — Conversations** (DL-0242, ADR-0055 Amendment 1). Persisted, ordered,
  tenant-scoped call history.
  - F-2: the console runtime writes Epic 10's durable log (`SqlDurableEventLog`, through
    `build_observability_plane`; no outbox, no new producer or event type).
    `build_webrtc_app(observability=…)` and `build_real_session_controller(observability=…)` are
    additive passthroughs.
  - F-3: the WebRTC app's transcript sink records seconds since session start (was always `0.0`);
    legacy rows are reported unordered.
  - F-7 (confirmed): on the product path each turn lost `voice_to_voice_ms` to an orphan metric.
    `AudioEndpointingController` enables audio-driven endpointing on the real profile
    (`VOXVOICE_CONSOLE_AUDIO_ENDPOINTING`).
  - `/v1/console/conversations` (filters, keyset pagination) and `/{id}` (transcript, per-turn
    latency, lifecycle timeline; unmeasured is never 0; no raw payloads). Console Conversations
    list and detail, "View this conversation" from the Playground, "Conversations" from an agent.
  - No dependency added. No pre-`v4.0.0` `api/` file or Epic 10 module modified.
- **M12.6 — Analytics** (DL-0243, ADR-0055 Amendment 2). Read-only aggregates over the
  conversation data, with a written contract
  ([`m12-analytics-definitions.md`](docs/architecture/epic12/m12-analytics-definitions.md)).
  - `GET /v1/console/analytics/{summary, latency, timeseries, agents}`: conversations by status
    and source, recorded/unrecorded, duration, turns by outcome, voice-to-voice and stage latency
    (`samples`, `missing`, avg/min/max, nearest-rank p50/p95), daily buckets including empty days,
    per-agent usage including archived agents. Range `today|7d|30d` or `from/to` (UTC days).
    Filters `agent_id` and `source`.
  - Missing is never 0: unmeasured latency, unfinished conversations and unrecorded conversations
    are reported as such. There is no cost, end-reason or barge-in figure, because the data does
    not support one.
  - Console Analytics page: summary, usage over time, latency table, agents and outcomes, each with
    loading, empty and error states. No chart dependency.
  - No table, event, writer, index, migration or dependency added. No pre-`v4.0.0` `api/` file or
    Epic 10 module modified.

- **M12.7 — API keys & developer docs** (DL-0244, ADR-0055 Amendment 3). The external developer
  contract, built on the one existing M11 key model.
  - `/v1/console/api-keys` (list/create) and `/{id}/revoke`, owner/admin only, CSRF on mutations.
    The raw key is returned once (`Cache-Control: no-store`) and only its SHA-256 is stored.
    Revoking deletes the `apikey` row, so the next developer-API request with it is `401`. No new
    table, migration or auth path: `dp_api_key_meta` is not built.
  - The console app's OpenAPI now declares `ApiKeyAuth`, `SignallingToken` and `ConsoleSession`,
    and marks every operation's credential and `x-voxvoice-surface`.
  - [`docs/apis/developer-api.md`](docs/apis/developer-api.md): the developer contract, covering
    credentials, conventions, agents, sessions, the WebRTC flow, errors, examples and what is not
    available. A test keeps its endpoint list equal to the OpenAPI developer surface.
  - Console: **API keys & docs** (`/developers`) with a one-time key display and copy, and
    **Developer docs** (`/developers/docs`) with a reference generated from `/openapi.json`.
    Navigation honours the server's declared capabilities.
  - No runtime, provider, analytics or frozen-file change. No dependency added.

- **M12.8 — Overview dashboard** (DL-0245). The console's landing page, composed from the
  existing product data.
  - `GET /v1/console/overview` (viewer+, console only): one request, one analytics snapshot, and
    the same view builders as `/v1/console/analytics/*`, so every figure is identical to Analytics
    (tested). Also the agent inventory (active/archived/unsupported language), the 5 latest
    conversations from the M12.5 list, and deterministic attention items. No new table, event,
    metric, score or cost.
  - Overview page: summary, needs-attention with actions, usage, latency snapshot (median, samples;
    "not measured"), recent conversations and agent activity (linked to their pages), runtime
    status and Get started. Range and agent filters.
  - M12.6's analytics view building was extracted into shared functions, with identical output.
  - **Fixed across the console:** switching organization left the previous organization's data on
    screen until you navigated away. Views are now keyed by organization, and URL filters are
    dropped on switch.
  - No runtime, provider, analytics-definition or frozen-file change. No dependency added.

### Fixed

- **M12.1.1 — Epic 10 E10-7 historical counting** ([ADR-0052 Amendment 2](docs/adr/ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md),
  DL-0238). E10-7 derived its `examined` digest input from the **live** count of `api/` changes
  since `v4.0.0`, so legitimate M11/M12 additions moved the Epic 10 digests (F-6: three tests
  failed on a clean `m11-local-baseline`). It now reads the certified `v4.0.0..db8c508` diff
  (2 paths), and violations are still read from the live tree. The certification digest
  `7e57bc14…` and freeze digest `d3d4858d…` reproduce exactly. **No re-baseline**, no Epic 10
  runtime change, no control removed.

## Unreleased — Epic 10 (Durable Event & Telemetry Spine) · 🟢 Implemented (M10.2–M10.12) · ✅ Certified (M10.13) · 🔒 Epic 10 architecture FROZEN (M10.14) · 📦 **RELEASED v5.0.0** (M10.15)

### Added

- **Epic 10 Repository Release — Durable Event & Telemetry Spine v1.0 · v5.0.0 (M10.15)** — the
  release commit and annotated tag `v5.0.0`, plus
  `docs/releases/epic10-durable-event-telemetry-spine-v1.0.md`. **Contributes repository-release
  metadata only**: no event type, no table, no migration, no route, no port method, no production
  module and no version-file change — the annotated tag is the single release identity, as it was
  for v1.0.0, v2.0.0, v3.0.0 and v4.0.0, and `platform/pyproject.toml` remains `version = "0.1.0"`.
  **Discharges M10.14's deferred commit**: owner ruling **R-2** froze the architecture without
  committing, so the freeze manifest recorded a two-part `commit_state` — the certified
  implementation committed at `db8c508`, its own freeze artifacts intentionally uncommitted — and
  this commit preserves them, discharging roadmap risk **D-11** (*"implementation exists but is
  uncommitted at freeze"*) without repeating it. Freeze verdict **`FROZEN`** (manifest `m10.14.1`,
  digest `d3d4858d5f18b7f6…`, 12 attestations `EF-1`…`EF-12` all satisfied) and certification
  verdict **`certified`** (catalogue `m10.13.1`, digest `7e57bc14764201b1…`, 13 controls
  `E10-1`…`E10-13`, 0 blocking violations, 0 advisories, 0 inconclusive) are both **preserved
  unchanged and consumed as evidence** — M10.15 re-certified nothing and re-froze nothing. Epic 9's
  historical digests `eed692224f0aeb82…` and `39790b95d061a763…` are quoted as Epic 9 history only,
  never recomputed. Migration head remains **`0036`**; migrations `0001`–`0033` unchanged;
  `platform/billing/**` unchanged since `v4.0.0` apart from the single authorized
  `MeterSource.OBSERVABILITY` member (ADR-0039 Amendment 1, MB14). **No advisory was fixed and no
  limitation was closed** — Epic 9's `NS-5` and `ST-3` remain open advisories with their original
  owners, Epic 9's nine limitations remain Epic 9's, and Epic 10's four declared limitations
  (`E10-LIM-1`…`E10-LIM-4`) all remain open. **No performance baseline was created and Epic 10
  makes no latency claim**: ADR-0042 HD2/HD3 rule out benchmarking here and record **I1 as a
  standing Epic 11 obligation**, `docs/performance/` does not exist, and nothing in this release
  asserts a voice-to-voice budget is met. Ruff cleanliness is claimed only for the Epic 10 DoD
  scopes `ruff check . --exclude voicebot` and `ruff check billing/ tests/`; `voicebot/bot.py`
  carries two pre-existing findings outside Epic 10's scope. Rationale **DL-0146**; no ADR-0044 was
  required.

## Unreleased — Epic 9 (Billing & Subscription Platform) · 🟢 Implemented (M9.0A–M9.25) · 🔒 Epic 9 architecture FROZEN (M9.25) · 📦 **RELEASED v4.0.0** (M9.26)

The opening of **Epic 9**: an architecture blueprint and one ratified ADR fixing the
permanent ownership boundary of revenue **before** implementation begins. No code, no APIs,
no schemas, no migrations, no UI, and **no change to any frozen Epic** (5/6/7/8 remain
byte-for-byte unchanged).

### Added

- **Epic 9 Billing Platform Blueprint (M9.0A)** — `docs/architecture/epic9/billing-platform-blueprint.md`.
  Design-only blueprint delivering all 28 requested artifacts (ownership, revenue,
  subscription, metering, pricing and entitlement models; billing/payment/invoice/refund
  lifecycles; tax incl. GST/VAT; marketplace; enterprise licensing; reseller/white-label
  strategy; integrations with Epic 8 / Product Runtime / Deployment / Observability;
  security, compliance, scalability; a 12-item risk register; 7 recommended ADRs; and a
  20-milestone roadmap M9.0A–M9.19 on a revenue spine with four release gates).
  Organizing invariant: **Billing owns revenue and nothing else.** Keeps Observability's
  **estimated** cost and Billing's **authoritative** charge architecturally separate.
  Indexed in `docs/INDEX.md`; rationale **DL-0107**.
- **Epic 9 Repository Release — Billing Platform v1.0 · v4.0.0 (M9.26)** — the release commit and
  annotated tag `v4.0.0`, plus `docs/releases/epic9-billing-platform-v1.0.md` finalized with release
  notes. **Contributes repository-release metadata only**: no financial fact, no table, no
  migration, no route, no Studio page, no production module, and no version-file change — the
  annotated tag is the single release identity, as it was for v1.0.0, v2.0.0 and v3.0.0.
  **Discharges M9.25's FZ-12**: the Epic 9 tree was entirely uncommitted at freeze (roadmap risk
  **D-11**, the same condition Epic 8 recorded at M8.20 §22), so the freeze described a working
  directory no commit preserved; one commit now preserves the whole frozen epic and the repository
  is reconstructable from it. Certification digest `39790b95d061a763…` and freeze manifest digest
  `eed692224f0aeb82…` both **preserved unchanged**; migration head remains **0033**; 27 controls, 24
  blocking, 0 blocking violations, 0 inconclusive. **Neither advisory was fixed and no limitation
  was closed** — NS-5 and ST-3 remain open advisories with named owners, and all nine declared
  limitations remain owned by the milestones that can close them. One release blocker was found and
  resolved before committing: an M9.25 freeze assertion tested the *absence* of `v4.0.0`, which is
  not a property of a frozen tree; it was relocated to the M9.26 release suite inverted and
  strengthened (the tag must exist, be annotated, and point at the release commit). 22 release
  tests. Rationale **DL-0137**.
- **Epic 9 Architecture Freeze (M9.25)** — `tests/freeze/manifest.py`,
  `tests/test_billing_freeze.py`, `docs/architecture/epic9/freeze.md` and
  `docs/releases/epic9-billing-platform-v1.0.md`. Freezes the exact state M9.24 certified.
  **Introduces no financial fact, no table, no migration, no route, no Studio page and no
  production module.** The freeze manifest is content-addressed and deterministic — digest
  `eed692224f0aeb82…` — split into **derived** values (recomputed from the tree) and **declared
  release inputs** (the regression and ruff results, which are facts about running the suite rather
  than reading the tree), because a manifest that claimed to derive them would overstate what it can
  prove. Twelve freeze invariants **FZ-1..FZ-12**, all asserted and failing closed; migration head
  pinned at **0033**; certification digest pinned at `39790b95d061a763…` so a drifting tree fails
  the freeze rather than re-certifying quietly. **FZ-12 is not satisfied and says so**: Epic 9 is
  entirely uncommitted (`HEAD` is Epic 8's release), which is roadmap risk **D-11**, anticipated by
  the freeze/release split and discharged by **M9.26**. Two determinism defects found by the freeze
  and fixed at the owning layer: the certification digest was context-dependent because `NS-2`
  counted every index in the process rather than Billing's, and the manifest repeated the same
  defect class. Neither changed the certification catalogue, a severity or a clause; the corrected
  digest is **identical** to M9.24's recorded value. 29 freeze tests. **M9.26 remains the Repository
  Release milestone** — commit, tag, baseline v4.0.0. Rationale **DL-0136**.
- **Billing Certification (M9.24)** — `tests/certification/{catalogue,runner}.py` and
  `tests/test_billing_certification.py`. **Adds zero production code**: `billing/` is unchanged by
  this milestone apart from the shadowing renames. The
  final domain milestone, specified by the roadmap as *"independent certification against ADR-0023,
  the Blueprint and the Provider Strategy"* with the DoD **"certification is reproducible as tests,
  not a one-off document."** **M9.24 owns no financial fact**: it reads module graphs, package
  namespaces, schema metadata, route tables, FSM definitions and the migration chain, and returns a
  verdict. It ships **no table, no migration, no repository, no command bus, no customer API and no
  Studio page**, and lives outside `billing/` entirely — each absence deliberate, because a stored verdict is a staler answer than
  re-running the suite and an org-scoped endpoint for a repository-wide engineering fact would be a
  lie about ownership. 27 controls (24 blocking, 3 advisory) across eight families, each citing a
  ratified clause; a control that cannot gather its evidence is **`INCONCLUSIVE`, never a pass**,
  and an inconclusive blocking control fails certification. Verdict:
  **`CERTIFIED_WITH_ADVISORIES`** — 0 blocking violations, 0 inconclusive, 2 owned advisories
  (`NS-5` 29 module-qualified OpenAPI schema names, `ST-3` two Studio pages rescaling by a
  hardcoded exponent), 9 declared limitations each with an owning milestone. The audit found and
  fixed **10 colliding names across 21 definitions** spanning M9.12–M9.22 — including two identity
  functions (`derive_evidence_digest`, `derive_policy_version_id`) each defined twice under one
  name — closing
  structurally the defect class M9.20, M9.21, M9.22 and M9.23 had each shipped. 71 tests. Migration
  chain unchanged at **0033**. Rationale **DL-0135**.
- **Billing Period Scope & Consolidation Engine (M9.23)** — `billing/foundation/{commercial_consolidation,consolidation_rules,consolidation_factories}.py`,
  `billing/application/consolidation_engine.py`, `billing/persistence/{consolidation_models,consolidation_repositories}.py`,
  migration `0033`, `billing/customer/{consolidation_schemas,routers/consolidation_engine}.py`, and
  `billing/customer/studio/consolidations.html`. Which accounts belong to a reporting scope, for
  which period, at which source revisions — and what could not be included, and why. **Versioned
  scope definitions** with five modes whose semantics are declared as data; **immutable scope
  snapshots** so an account that moves parent in March never changes what January's consolidation
  covered; **pinned source bindings**, one row per `(account, domain)`, so a hundred-thousand-account
  scope paginates and a re-collection cannot double-count; **named exceptions**; **validation**;
  content-addressed **finalized results**; and **corrections** that land beside the original rather
  than rewriting it. Closes M9.22's headline gap: every period-scoped read Billing publishes is
  account-keyed, so a group filing needs a resolved scope first. **M9.23 owns scope and owns no
  financial fact** — M9.8 keeps the account hierarchy (consumed through a reader with no write
  method), M9.13–M9.18 keep their facts, M9.19 reconciliation, M9.20 correlation, M9.21
  investigation, M9.22 filing. Attribution is **never guessed**, missing accounts and unreadable
  sources **never become zero**, and currencies are **never combined implicitly or converted** —
  there is no FX rate at any altitude. 11 commands, 14 queries, 10 tables, 22 paths under
  `/consolidations`, 13 Studio screens, 121 tests. Rationale **DL-0134**.
- **Billing Compliance & Regulatory Reporting Engine (M9.22)** — `billing/foundation/{commercial_reporting,reporting_rules,reporting_factories}.py`,
  `billing/application/reporting_engine.py`, `billing/persistence/{reporting_models,reporting_repositories}.py`,
  migration `0032`, `billing/customer/{reporting_schemas,routers/reporting_engine}.py`, and
  `billing/customer/studio/reports.html`. What must be reported, for which period, under which
  versioned definition, from which authoritative sources: **versioned report definitions**,
  explicit **reporting periods** that name which of Billing's four period concepts they consume,
  immutable **input manifests** that pin every source at the revision it was read, **report runs**,
  **named exceptions**, **validation**, content-addressed **finalized snapshots**, **corrections**
  that never rewrite a filing, and **submission packages** that are prepared and never sent.
  **A report is derived evidence, not financial truth** — it creates no ledger entry, invoice, tax
  determination, payment or recognition entry, imports no repository that could, and has **no
  outward command at all**. Every field names its authoritative source; `ReportTransformation` is a
  closed set of seven of which exactly one touches money, and that one **refuses a mixed-currency
  input rather than converting it**. There is **no FX rate anywhere** and `CurrencyBasis` has no
  `CONVERTED` member. **Missing evidence never becomes zero** — `EMPTY` (we looked, nothing was
  there) stays distinct from `UNAVAILABLE` (we could not look) through the transformation, the
  completeness verdict, a named exception and the wire. Definitions are immutable once published;
  a filing stores the version that produced it, so no later rule reinterprets it. `FINALIZED` is
  reachable only from a validation that passed, and acknowledging an exception does not unblock a
  filing. No `DELETE`, `PUT` or `PATCH` on the surface. ADR-0023 PY5 unchanged, now holding at a
  fourth altitude.
- **Financial Investigation & Audit Evidence Engine (M9.21)** — `billing/foundation/{commercial_investigations,investigation_rules,investigation_factories}.py`,
  `billing/application/investigation_engine.py`, `billing/persistence/{investigation_models,investigation_repositories}.py`,
  migration `0031`, `billing/customer/{investigation_schemas,routers/investigation_engine}.py`, and
  `billing/customer/studio/investigations.html`. What a human actually found out about an integrity
  incident, on what evidence, and whether an auditor can reconstruct it **without relying on
  application logs**: durable **investigations**, an append-only **timeline**, content-addressed
  **evidence captures** with a **chain of custody**, **questions** with real outcomes,
  **observations** that keep facts and hypotheses apart, **conclusions** that are superseded rather
  than edited, and immutable **audit packages**. **An investigation records what was observed and
  concluded and never becomes the source of financial truth** — there is no balance, total, tax,
  price or recognized amount anywhere, `Money` is not imported, and an AST scan asserts no
  arithmetic touches a figure. **A reference is not a snapshot**: a `LIVE_REFERENCE` structurally
  carries no body and no digest and can never back a confirmed conclusion, a snapshot must carry
  both, and the evidence store has **no update path at all** — so neither becomes the other. Evidence
  is append-only: a source becoming unavailable, redacted or superseded appends a **new link** naming
  the one it follows, and nothing is ever replaced. **"Could not retrieve" never becomes
  "verified"** — an unavailable capture stays visible as a named gap, blocks closure, and makes an
  audit package `INCOMPLETE`. A hypothesis cannot be promoted to a fact; a `CONFIRMED` conclusion
  requires an available snapshot and `INCONCLUSIVE` requires none, so **nobody is forced to invent a
  root cause to close a case**. Closure is reachable **only** through a recorded conclusion, and a
  closed investigation is never reopened. Audit packages are content-addressed and immutable:
  exporting unchanged state twice returns the same package, and a package cannot contain the record
  of its own creation. Exactly one canonicalizer, which **refuses floats**. Closing the investigation
  is **not** resolving the incident — M9.20 keeps that, and M9.21 has **no outward command at all**.
  No `DELETE`, `PUT` or `PATCH` on the surface. ADR-0023 PY5 unchanged, now holding at a third
  altitude.
- **Financial Integrity Correlation & Audit Engine (M9.20)** — `billing/foundation/{commercial_incidents,incident_rules,incident_factories}.py`,
  `billing/application/incident_engine.py`, `billing/persistence/{incident_models,incident_repositories}.py`,
  migration `0030`, `billing/customer/{incident_schemas,routers/incident_engine}.py`, and
  `billing/customer/studio/incidents.html`. Which reconciliation findings belong to the same
  financial integrity incident, what the common evidence is, and whether an auditor can reconstruct
  the chain: durable **incidents**, evidence-backed **finding links**, an append-only **audit
  trail**, non-destructive **merges** and **correlation corrections**, and repair references that
  never claim to be resolutions. Closes the limitation M9.19 shipped with. **Correlation is an
  evidence relationship and never manufactures a root cause** — identity is extracted from a
  declared precedence over identifiers the owning domains already made immutable, and amount,
  timestamp, account, invoice number, description, customer and payment method correlate nothing
  alone or together. Precedence under-correlates rather than over-correlates, deliberately.
  `RootCause` declares no infrastructure causes and conflicting divergences classify as `UNKNOWN`;
  impact is classified, never calculated. Severity is **monotonic**. **Repair is never resolution** —
  no `REPAIR_IN_PROGRESS → RESOLVED` edge exists, and verification refuses when anything is still
  divergent *or unreadable*. History is never rewritten: merges and corrections are new facts, links
  are detached rather than deleted, and status is a projection over an append-only trail.
  **No balance, total, amount or currency exists anywhere in the milestone.** 11 commands + 14
  queries, 6 tables via migration 0030, 25 API paths under `/integrity/incidents` (396 total,
  path-disjoint from M9.19's), 10 Studio screens, 108 tests, 4186/4186 repository-wide, ruff clean.
  **Two defects found and fixed.** Indexed in `docs/INDEX.md`; rationale **DL-0131**.
- **Billing Reconciliation & Financial Integrity Engine (M9.19)** — `billing/foundation/{commercial_reconciliation,reconciliation_rules,reconciliation_factories}.py`,
  `billing/application/reconciliation_engine.py`, `billing/persistence/{reconciliation_models,reconciliation_repositories}.py`,
  migration `0029`, `billing/customer/{reconciliation_schemas,routers/reconciliation_engine}.py`, and
  `billing/customer/studio/integrity.html`. Whether the six financial domains agree with one
  another: durable reconciliation **runs**, **findings** and evidence-backed **resolutions** across
  six declared relationships (payment↔ledger, invoice↔ledger, invoice↔tax, invoice↔recognition,
  payment↔invoice, recognition↔ledger). **A reconciliation result is an observation, not permission
  to mutate another domain** — enforced structurally: no other domain's repository is imported,
  `PostJournalEntry` is provably unreachable, and the whole repair surface is a two-entry whitelist
  dispatching the *owning* domain's own command. The pre-implementation audit reused M9.2's
  `ReconciliationVerdict`, left M9.16's payment↔provider reconciliation entirely alone, and
  *delegated* invoice↔ledger and invoice↔tax to M9.14's and M9.15's own comparison services rather
  than writing a second opinion. Runs are identified by an **evidence digest** over exactly what was
  compared and nothing volatile, so two workers converge, an unchanged re-run writes nothing, and a
  re-run after a repair is genuinely a new observation. ADR-0023 **PY5 is unamended and now
  structural**: the finding lifecycle has no edge out of `RESOLVED` or `CLOSED`, and `resolve`
  re-reads both sides and refuses unless they agree. Severity is a declared table — a fabricated
  financial fact, a duplicate posting and an amount mismatch are always CRITICAL, and no divergence
  is ever informational. **No balance, total or monetary sum exists anywhere in the milestone.**
  10 commands + 15 queries, 5 tables via migration 0029, 25 API paths under `/integrity` (371
  total), 10 Studio screens, 109 tests, 4078/4078 repository-wide, ruff clean. **Three defects found
  and fixed**, including one that made the engine blind to its own headline case. Indexed in
  `docs/INDEX.md`; rationale **DL-0130**.
- **Revenue Recognition & Deferred Revenue Engine (M9.18)** — `billing/foundation/{commercial_revenue,revenue_rules,revenue_factories}.py`,
  `billing/application/revenue_engine.py`, `billing/persistence/{revenue_models,revenue_repositories}.py`,
  migration `0028`, `billing/customer/{revenue_schemas,routers/revenue_engine}.py`, and
  `billing/customer/studio/revenue.html`. When a billed amount becomes earned: immutable versioned
  recognition policies, schedules derived from **issued invoice snapshots** and identified by a
  **basis digest** covering every input that can change the answer, dated period entries,
  corrections as new facts, and an explanation assembled without consulting current pricing, tax,
  payment or subscription state. **Deferred revenue is never stored** — total minus recognized
  minus adjusted, derived on every read (ADR-0023 RS6: recognition is a projection over the ledger,
  not a second set of books). **Paid is not earned**: no payment or collection module is imported.
  Every split uses M9.2's largest-remainder allocator; conservation is asserted across nine
  schedule shapes. Calendar arithmetic is pure integers rather than `datetime`, so the Foundation's
  ambient-clock ban stayed intact, and it is proven against the stdlib. Closed accounting periods
  reuse M9.13's `LateEntryDisposition`; a Ledger refusal produces an `UNPOSTED` recognition and a
  repair queue. M9.2's `RecognitionState` and M9.13's `AccountingPeriod` are untouched;
  `PostingSource.REVENUE_RECOGNITION` and `PostJournalEntry.recognition` are additive extensions.
  10 commands + 16 queries, 5 tables, 24 paths under `/revenue`, 110 tests, 3969/3969
  repository-wide, ruff clean. Two defects found and fixed. Rationale **DL-0129**.
- **Dunning & Collections Engine (M9.17)** — `billing/foundation/{commercial_collections,collection_rules,collection_factories}.py`,
  `billing/application/collections_engine.py`, `billing/persistence/{collection_models,collection_repositories}.py`,
  migration `0027`, `billing/customer/{collection_schemas,routers/collections_engine}.py`, and
  `billing/customer/studio/recovery.html`. Recovery **policy**: immutable versioned strategies, one
  case per obligation, durable actions decided before anything leaves the building, append-only
  execution evidence, and a decision trail that replays after the strategy is rewritten.
  **Collections decides whether to ask; Payments moves the money; Ledger records the fact.** It
  reaches no provider, posts no ledger entry, recalculates no invoice total, and holds no balance —
  there is no amount anywhere in the domain. An *ambiguous* payment outcome is never a retry
  candidate (three independent gates, inheriting the M9.16-R1 invariant). Case transitions are
  compare-and-set against a `version`, so a stale worker cannot undo a recovery; one obligation is
  one case by derived primary key. Billing runs **no scheduler** — what is due is a query.
  M9.2's `DunningProcess`, `DunningPolicy` and `cp_billing_dunning` are untouched, and its three
  `billing.dunning.*` event types are **reused rather than renamed**. 12 commands + 15 queries,
  6 tables, 25 paths under `/recovery`, 97 tests, 3859/3859 repository-wide, ruff clean.
  Four defects found and fixed. Indexed in `docs/INDEX.md`; rationale **DL-0128**.
- **Payment Engine (M9.16)** — `billing/foundation/{commercial_payments,payment_rules,payment_factories}.py`,
  `billing/application/payment_engine.py`, `billing/persistence/{payment_models,payment_repositories}.py`,
  migration `0026`, `billing/customer/{payment_schemas,routers/payment_engine}.py`, and
  `billing/customer/studio/payments.html`. External money movement: collection objectives,
  append-only attempts, the durable provider-operation claim, verified provider callbacks,
  refunds as new financial facts, reconciliation, diagnostics, search and an append-only audit
  trail — 14 commands + 17 queries, 8 tables, 30 API paths under `/collections`, an 11-screen
  Studio. **The provider moves money; Billing owns the financial fact that it moved** (ADR-0023
  PY1/PY4, M9.0C). `PaymentIntent` is split from append-only `PaymentAttempt`, so a retry appends
  attempt *n+1* rather than rewriting attempt *n*. The money-moving path is **three phases with
  the provider call between two transactions**, with the operation row committed first under a
  **composite primary key on `(org_id, operation_key)`** — so two workers produce one claim and
  the loser retrieves instead of sending. A timeout becomes `AMBIGUOUS`, which **blocks any
  further attempt** until reconciliation retrieves the same key. Neither FSM has an edge out of a
  settled state. **No provider is named anywhere**; routing is operator-declared and the migration
  seeds nothing. 133 tests including 25 named demonstrations; rationale **DL-0126**.
- **Payment Engine corrective hardening (M9.16-R1)** — closes the three findings an independent
  review rejected M9.16 for. **S0:** a successful provider *void* was read through the
  money-movement table and recorded as a **capture**, posting a ledger credit for money never
  collected; outcome interpretation is now action-aware (`ACTION_TRIGGERS`, with `CANCEL` mapping
  `SUCCEEDED` to a cancellation and `REFUND` moving no attempt state) and the Ledger is reached only
  behind `asserts_money_moved`. **S1:** a *raised* provider call left an attempt in `started`,
  blocking retry, cancellation and reconciliation permanently; every provider call now yields
  `AMBIGUOUS` unless the adapter raises `ProviderNotSent` to prove nothing was transmitted, and
  reconciliation resolves any externally-open attempt. **S2:** the refund ceiling was a
  read-modify-write two different request keys could walk past; `(org_id, attempt_id, sequence)` is
  now unique so the database arbitrates, verified by two Applications over two connections to one
  file-backed database. Also: `PARTIALLY_PAID`/`SETTLE_PARTIAL` removed as unreachable, the ledger
  exception boundary narrowed to `BillingError`, an ambiguous attempt is no longer voidable, and
  the event endpoint is renamed and documented as an **internal relay** — M9.16 is explicitly not
  ADR-0023 PY4 webhook compliance. 28 new tests (161 total); rationale **DL-0127**.
- **Tax Determination Engine (M9.15)** — `billing/foundation/{commercial_tax,tax_rules,tax_factories}.py`,
  `billing/application/tax_engine.py`, `billing/persistence/{tax_models,tax_repositories}.py`,
  migration `0025`, `billing/customer/{tax_schemas,routers/tax_engine}.py`, and
  `billing/customer/studio/taxation.html`. Tax treatment of already-determined amounts:
  jurisdictions, classifications, supplier nexus and registrations, customer exemptions,
  **effective-dated rate evidence** carrying the authority that set it, deterministic
  place-of-supply resolution, declared treatment precedence, integer-only calculation with a
  recorded rounding mode and scope, attributed overrides, immutable content-addressed
  determinations that supersede rather than edit, an explanation that answers *why was this tax
  applied?* **without calling the provider again**, reconciliation, diagnostics, search, and an
  append-only audit trail. 13 commands + 16 queries, 7 tables, 24 API paths under `/taxation`, an
  11-screen Studio, and 19 named determination demonstrations. **Billing declares no rate
  anywhere** (ADR-0023 TX1): an AST scan forbids an integer literal reaching `rate_basis_points=`,
  the migration seeds nothing, and a fresh installation determines no tax until an operator declares
  one behind the `TaxRateProvider` adapter port. `TaxAssessment.to_determination()` is the single
  projection onto M9.2's `TaxDetermination`, so **M9.14 needed no change to start presenting tax**.
  Five defects found and fixed, four by the end-to-end smoke and two financially material in
  opposite directions: CGST and SGST collided on the derived rate id, which would have taxed every
  Indian intra-state invoice at half the correct rate; and inclusive pricing extracted per component
  instead of against the combined rate, which would have over-collected on every tax-inclusive sale.
  Two frozen assertions **sharpened, not weakened** — a parameter named `country` contains the
  substring `try:`. Repository-wide 3601 tests passing, zero regressions.

- **Invoice Engine (M9.14)** — `billing/foundation/{commercial_invoicing,invoice_rules,invoice_factories}.py`,
  `billing/application/invoice_engine.py`, `billing/persistence/{invoice_models,invoice_repositories}.py`,
  migration `0024`, `billing/customer/{invoice_schemas,routers/invoice_engine}.py`, and
  `billing/customer/studio/invoices.html`. Customer documents: invoices, credit notes and debit
  notes with **gapless, jurisdiction-aware numbering allocated inside the issuing transaction**,
  immutable **content-addressed** snapshots that reproduce byte-identically forever, append-only
  revision history, corrections by credit and debit note rather than by editing, document
  relationships recorded on both sides, deterministic HTML and PDF document-model rendering that
  computes nothing, delivery and attachment **metadata only**, structured export, inspection,
  diagnostics, reconciliation against the ledger, and append-only history. 13 commands + 14 queries,
  7 tables, 26 API paths under `/documents`, an 11-screen Studio, and 19 named document
  demonstrations. Consumes a **closed** accounting period and refuses an open one — a document is a
  statement of a figure that has stopped moving. Reuses M9.2's `InvoiceState`, `ChargeLineKind`,
  `InvoiceNumber`, `InvoiceTotalsService` and `InvoiceNumberFormatter` unchanged, each asserted to
  remain the only declaration. Five defects found and fixed, two by the end-to-end smoke: all three
  document kinds printed the same number because each counts in its own series and the format
  carried no kind token; and a revision silently discarded the supplier, template and payment
  instructions the draft was prepared with, and would then have been issued that way. One frozen
  assertion **sharpened, not weakened** (carrying a Foundation value is not duplicating a Foundation
  rule), following the M9.7 precedent. Repository-wide 3449 tests passing, zero regressions.

- **Credit & Revenue Ledger (M9.13)** — `billing/foundation/{commercial_ledger,ledger_rules,ledger_factories}.py`,
  `billing/application/ledger_engine.py`, `billing/persistence/{ledger_models,ledger_repositories}.py`,
  migration `0023`, `billing/customer/{ledger_schemas,routers/ledger_engine}.py`, and
  `billing/customer/studio/ledger.html`. Financial recording: immutable append-only journal entries
  with **exactly-once posting keyed by what caused them**, balances **derived on every read and
  never stored**, accounting periods with a one-way close and a sealed content digest a replay
  proves against, corrections by reversal and adjustment rather than mutation, credits whose
  remaining value is derived from their applications and which are spent earliest-expiring first, a
  trial-balance control report, reconciliation against rated charges, balance checkpoints that are
  caches with proofs, inspection, diagnostics, and append-only history. 12 commands + 14 queries,
  7 tables, 24 API paths under `/accounting`, an 11-screen Studio, and 19 named accounting
  demonstrations. Declares itself an **accounts-receivable subledger** rather than a general ledger,
  with `TrialBalance` as the reconciliation surface — recommended for ratification into ADR-0023.
  Implements the guard M9.12's spec recorded as missing: **a provisional rating run is refused**.
  Four defects found and fixed, two by the end-to-end smoke: the deferral path was unreachable
  because a posting request never named the period it belonged to, so a late correction would have
  posted silently into the open period losing its audit signal; and corrections shadowed their
  originals during reconciliation, making every corrected charge read as an amount mismatch.
  Repository-wide 3293 tests passing, zero regressions.

- **Rating Engine (M9.12)** — `billing/foundation/{commercial_rating,rating_rules,rating_factories}.py`,
  `billing/application/rating_engine.py`, `billing/persistence/{rating_models,rating_repositories}.py`,
  migration `0022`, `billing/customer/{rating_schemas,routers/rating_engine}.py`, and
  `billing/customer/studio/rating.html`. Commercial charge calculation: flat, seat, per-unit,
  allowance/overage, graduated, volume, package and hybrid charges, each with a breakdown that sums
  **exactly** to the charge and provenance naming every frozen artefact that produced it;
  entitlement-over-price allowance precedence; minimum commitments applied after every other charge;
  deterministic rating runs whose id is **derived from an input digest**, making rerating idempotent
  at the database and replay byte-comparable; a draft→rated→finalized lifecycle with **no edge out of
  finalized**, so a finalized figure is *adjusted* and a provisional one *superseded*; comparison,
  explanation, inspection, snapshots, diagnostics, search, and append-only history. 9 commands +
  12 queries, 5 tables, 19 API paths under `/charges`, an 11-screen Studio, and 19 named correctness
  demonstrations. All money is integer minor units — no float participates anywhere.
  **Fixes a genuine defect in frozen M9.7 additively:** every discount, including fixed sums, is
  folded into the *unit* rate, so a one-time "₹500 off this line" became "₹500 off each unit" once
  rating multiplied by quantity — ₹1,000 billed where ₹5,500 was owed. Rating re-bases the rate,
  applies the fixed sum once at charge level, and shows the correction in the breakdown; M9.7 is
  untouched and the result is arithmetically identical wherever it was already right.
  Repository-wide 3158 tests passing, zero regressions.

- **Metering Engine (M9.11)** — `billing/foundation/{commercial_metering,metering_rules,metering_factories}.py`,
  `billing/application/metering_engine.py`, `billing/persistence/metering_{models,repositories}.py`,
  `billing/customer/{metering_schemas.py,routers/metering_engine.py,studio/metering.html}`
  (+ spec at `docs/architecture/epic9/metering-engine.md`). The domain that owns **commercial
  usage records and nothing else** — the highest-risk correctness milestone of Epic 9, because it
  is the only one whose errors are silent.
  **Billing never measures.** Signals arrive through `MetricStreamPort`, an ABC declared inside
  this plane and implemented outside it: no producer import, no polling, no reach into runtime
  state, and no outbound call of any kind (AST-asserted). The port has **no emit, publish, or
  write method**, so Billing is structurally incapable of producing an operational metric; an
  unwired port ingests nothing rather than inventing usage.
  **Exactly one authoritative usage record per source event**, defended four deep: a *derived*
  primary key, a read-before-write, a database unique constraint, and an `IntegrityError` re-read
  inside a `SAVEPOINT` — so two instances racing on one event are indistinguishable from one
  processing it twice, and the loser's transaction survives intact.
  Two unusual, deliberate decisions: a usage id is **derived** rather than minted, so replay
  produces byte-identical usage including ids; and a sealed period carries a **seal digest** over
  its billable content, so a replay months later *proves* it reproduced the same commercial usage
  rather than asserting it.
  Owns versioned meters with exactly one authoritative source (two meters claiming one signal is
  rejected — the double-counting failure), exact integer normalization with declared rounding
  (no float touches a quantity, `HALF_EVEN` available so meters are not biased in VoxVoice's
  favour), total attribution with an alerting quarantine, a declared late-arrival policy,
  windowed aggregation anchored to the billing period, **one-way sealing**, append-only
  corrections that never edit, deterministic replay using the *same* pure ingestion decision as
  the live path, continuous reconciliation, provenance, diagnostics, and byte-identical exports.
  Owns **no** money, ledger, invoice, payment, or entitlement (AST-asserted).
  Fourteen explicit correctness demonstrations — deterministic replay, duplicate suppression,
  idempotency under concurrent ingestion, replay after restart with a different id generator and
  clock, sealing rules, late-event determinism, multi-instance aggregate convergence, append-only
  history, and independence from Runtime availability.
  17 commands / 12 queries, 7 tables via **migration 0021**, 26 API paths under a separate
  `/usage` prefix (M9.5's `/metering` untouched), an 11-screen Studio, 184 tests. Additive
  throughout: `metering_engine` defaults to `None` on both `BillingRepositories` and
  `BillingContext`; M9.2's `metering.py` and its three usage tables are untouched; 18 new
  `billing.*` events, 20 new diagnostic codes; foundation exports 429→510. **Five defects found
  and fixed**, four in the correctness core (usage could land in a sealed period; an adjustment
  landed back in the period it adjusted; two overlapping meters could both go live; a correction
  was swallowed by the exactly-once constraint). Rationale **DL-0121**.
- **Entitlement Engine (M9.10)** — `billing/foundation/{commercial_entitlements,entitlement_rules,entitlement_factories}.py`,
  `billing/application/entitlements_engine.py`, `billing/persistence/entitlement_{models,repositories}.py`,
  `billing/customer/{entitlement_schemas.py,routers/entitlement_engine.py,studio/entitlements.html}`
  (+ spec at `docs/architecture/epic9/entitlement-engine.md`). The domain that answers *what is
  this billing account entitled to right now, and what should an enforcing plane do if it cannot
  see fresh data* — the platform unlock every other plane consumes. Implements the load-bearing
  Epic 9 invariant as **code shape**: Billing decides, Billing publishes immutable versioned
  snapshots, the owning platform enforces locally, and **Billing never performs authorization**
  (no module contains `principal`, `Permission`, `scope`, `has_role`, `check_permission`,
  `is_authorized`, `RoleAssignment`, or `RBAC` — a test greps for every one).
  Owns a gate registry where **every capability carries a fail-open / fail-closed / degrade
  classification** (an unclassified gate is an *error*, ADR-0023 E3), nestable bundles that report
  cycles as the path that closes them, precedence-resolved grants, half-open effective dating,
  scheduled intent, deterministic snapshot composition, staleness directives, inspection, and
  diagnostics. Owns **no** money, usage, ledger, invoice, payment, or tax (AST-asserted).
  Three structural decisions: M9.2's `GateClassification` / `GrantSource` / `GRANT_PRECEDENCE`
  are **reused, not re-declared** (§A2.5); `EntitlementSet.to_snapshot()` projects onto M9.2's
  frozen `EntitlementSnapshot`, so a plane written against the M9.5 contract consumes an M9.10
  decision unchanged; and grants live inside the profile aggregate because precedence is only
  correct when it sees all of an account's grants at once.
  Grant lifecycle draft→active→suspended→revoked/expired→archived with **no deletion state at
  all** and no `revoked→active` (re-granting is a new grant, or 'was this ever revoked?' becomes
  unanswerable). **Effective dating never mutates state** — a closed window simply stops being
  composed — which is why this domain contains no scheduler; revocation closes the *window* as
  well as the state so a later snapshot cannot re-admit the grant. Resolution is fully ordered
  (declared precedence → generosity → grant id) and order-independent, asserted by rotating the
  grant list through every offset; every answer carries a `resolution_path`, and inspection keeps
  the **losing** grants because 'why *not*?' is unanswerable from the winner alone. Snapshots are
  returned as published, never recomputed, with a pure `preview` for 'what would be published';
  republishing an unchanged decision is refused.
  19 commands / 12 queries, 5 tables via **migration 0020**, 28 API paths under a separate
  `/capabilities` prefix (M9.5's `/entitlements` untouched and unshadowed), an 11-screen Studio,
  199 tests. Additive throughout: `entitlements_engine` defaults to `None` on both
  `BillingRepositories` and `BillingContext`; M9.2's entitlement module and tables are untouched;
  18 new `billing.*` events, 22 new diagnostic codes; foundation exports 371→429. Rationale
  **DL-0120**.
- **Subscription Engine (M9.9)** — `billing/foundation/{commercial_subscriptions,subscription_rules,subscription_factories}.py`,
  `billing/application/subscriptions_engine.py`, `billing/persistence/subscription_{models,repositories}.py`,
  `billing/customer/{subscription_schemas.py,routers/subscription_engine.py,studio/subscriptions.html}`
  (+ spec at `docs/architecture/epic9/subscription-engine.md`). The domain that answers *what
  commercial agreement currently exists between a billing account and VoxVoice*, binding the three
  domains before it: **this account** (M9.8), on **this published catalog version** (M9.6), at
  **this published pricing version** (M9.7).
  All three references are immutable integer pins into frozen snapshots — never live objects, and
  deliberately **not** foreign keys, because a foreign key would make the two version lines a
  coupling the database enforces when the whole point is that they version independently. Tests
  assert both directions: publishing a new catalog never mutates an existing subscription, and
  publishing new pricing never mutates one either.
  It owns **no money at all**: `Money`, `minor`, `PriceTier`, `.times(`, `.allocate(`, and
  `.scaled(` appear nowhere across the eight modules, no amount reaches the wire, and the Studio
  has no money formatter — an agreement records what was *agreed*, and turning that into a charge
  is rating (M9.11).
  Adds a `draft → trialing/active → past_due/suspended/paused → cancelled → expired → archived`
  FSM on `core.fsm` with **no deletion state at all** — stronger than the catalog, pricing, and
  account lifecycles, because a subscription *is* the commercial agreement and ADR-0023 RS4 makes
  its record permanent; no `cancelled → active` (winning a customer back is a new agreement, and
  conflating them makes churn unmeasurable); no path between pause and suspension (one is the
  customer's choice, the other ours); and **renewal deliberately absent from the FSM** — it
  advances the billing cycle rather than changing state, with a test asserting the machine
  contains no self-loops at all.
  Scheduled changes persist **intent with no scheduler**: there is no timer, no worker, and no
  auto-apply, and a test reads due changes at a far-future instant and asserts the agreement is
  untouched — keeping "what we agreed" auditable independently of "what the system managed to
  run". Versions embed the whole agreement and roll **forward** with recorded provenance;
  freezing is deterministic, so two freezes of unchanged terms are byte-identical.
  Six item kinds (flat, seat, usage, tiered, volume, hybrid) live on **one** agreement rather than
  several subscriptions per customer. 23 commands / 6 queries on the existing buses, 4
  `cp_billing_*` tables via migration **0019** (two append-only), 25 API paths under a separate
  `/agreements` prefix so M9.5's `/subscriptions` wildcard cannot shadow them, and a 10-screen
  Studio. 165 new tests; repository-wide **2628 passing**, zero regressions. Rationale **DL-0119**.
- **Billing Accounts (M9.8)** — `billing/foundation/{commercial_accounts,account_rules,account_factories}.py`,
  `billing/application/billing_accounts.py`, `billing/persistence/account_{models,repositories}.py`,
  `billing/customer/{account_schemas.py,routers/billing_accounts.py,studio/accounts.html}` (+ spec at
  `docs/architecture/epic9/billing-accounts.md`). The domain that answers *who has the commercial
  relationship with VoxVoice*: commercial identity, billing hierarchy, payer relationships,
  contacts, addresses, legal entity, billing and invoice preferences, a tax-profile reference,
  credit profile and limit, commercial status, notes, activity, and history. It owns **no**
  subscription, price, catalog entry, usage record, payment, runtime object, or deployment —
  AST-asserted — and holds **no balance**: a credit limit is what we are willing to be owed, so
  `CreditProfile.permits(exposure)` takes the exposure as an argument and `Money.times`/`allocate`/
  `scaled` appear nowhere in the domain.
  Two separations are load-bearing. The **billing hierarchy is not Epic 8 tenancy** (ADR-0023 §5.2):
  `org_id` is a string this domain scopes on and never dereferences, and a test asserts no module
  imports `api` or mentions `Organization`/`Workspace`/`Project` — so a reseller hierarchy provably
  requires no change to organization tenancy. The **payer graph is not the parent graph**: a test
  constructs a parent whose payer is its own child and asserts neither graph is malformed, which is
  the modelling error this domain exists to avoid.
  Adds a `draft→active→suspended→closed→archived→deleted` FSM on `core.fsm` with **exactly one edge
  into `deleted`, and it starts at `draft`** — everything past draft has revenue attached, and
  ADR-0023 RS4 says revenue outlives the customer record (the whole machine is enumerated to prove
  it). `restore` returns an archived account to *closed*, not active, because un-filing a record and
  reopening a relationship are separate decisions. Closed and archived accounts are read-only,
  except for notes — a note is a record *about* the relationship, not a change to it.
  Cycles and payer loops are reported as the offending **path** (`a → b → c → a`), hierarchy depth
  is bounded at 6, and tree traversal terminates even on malformed data. Account numbers come from a
  stored per-organization sequence rather than `max + 1`, so deleting a draft never hands its number
  back. 14 commands / 5 queries on the existing buses, 3 `cp_billing_*` tables via migration **0018**
  (one append-only), 17 API paths under a separate `/billing-accounts` prefix so M9.5's `/accounts`
  wildcard cannot shadow them, and a 9-screen Studio. 130 new tests; repository-wide **2463
  passing**, zero regressions. Rationale **DL-0118**.
- **Pricing Engine (M9.7)** — `billing/foundation/{price_books,pricing_rules,pricing_factories}.py`,
  `billing/application/pricing.py`, `billing/persistence/pricing_{models,repositories}.py`,
  `billing/customer/{pricing_schemas.py,routers/pricing_engine.py,studio/pricing.html}` (+ spec at
  `docs/architecture/epic9/pricing-engine.md`). The domain that answers *what does this catalog
  version cost*: price books, price-book versions, currencies, regional pricing, effective dating,
  price resolution, overrides, discounts, promotions, coupons, and pricing history.
  Organizing boundary, **asserted rather than intended**: pricing answers what something *costs*,
  never what someone *owes* — `Money.times` and `Money.allocate`, the operations that turn a price
  into a charge, appear nowhere in the domain, and resolving the same price at `quantity=1` and
  `quantity=1000` returns the identical answer because quantity is an *eligibility* gate for
  volume discounts. Rating is M9.11.
  Adds a `draft→published→archived→deleted` FSM on `core.fsm` with **no `published→draft` edge** —
  repricing publishes a new revision, so the book an old invoice was rated against survives
  byte-for-byte; half-open `[from, until)` effective dating with collision detection (same code and
  *same version* overlapping, since a different version is how a superseding price is expressed);
  a deterministic resolver ranked by scope specificity → version → code that returns a
  `resolution_path`; a discount engine ordered by `(priority, code)` where each reduction comes off
  the **list price** (two 25% discounts are worth 50%, not 43.75%), a non-stackable discount ends
  the chain, and the total floors at zero; promotions and coupons that embed their own reduction
  (eligibility only — Pricing never redeems anything or counts a redemption); and immutable
  per-book and org-wide version lines whose rollback **publishes forward** with recorded
  provenance. Percentages are integer basis points and no float touches money anywhere
  (AST-asserted). Pricing pins to a *published catalog version by number*, so changing a price
  never republishes the catalog and changing the catalog never republishes prices.
  12 commands / 10 queries on the existing buses, 7 `cp_billing_*` tables via migration **0017**
  (three append-only; no money in any projection column), 18 API paths with **no collision** with
  M9.5's `/pricing/price-books`, and a 9-screen Pricing Studio. 144 new tests;
  repository-wide **2333 passing**, zero regressions. Rationale **DL-0117**.
- **Commercial Catalog (M9.6)** — `billing/foundation/{products,catalog_rules,catalog_factories}.py`,
  `billing/application/catalog.py`, `billing/persistence/catalog_{models,repositories}.py`,
  `billing/customer/{catalog_schemas.py,routers/catalog_admin.py,studio/}` (+ spec at
  `docs/architecture/epic9/commercial-catalog.md`). The domain that answers *what do we sell*:
  products, editions, plans, features, feature groups, bundles, and commercial metadata.
  Organizing boundary, **asserted rather than intended**: the Catalog owns commercial products
  and owns **no price, subscription, invoice, entitlement, or usage** — not one catalog type
  carries a `Money`, currency, amount, or rate. Adds 9 aggregates and a
  draft→published→deprecated→archived→deleted FSM on `core.fsm` (a published product cannot be
  deleted in one step; `deleted` is terminal and recorded, never a row removal); a validator that
  never raises and reports **every** finding, including bundle cycles **as the offending path**;
  deterministic search with no scoring engine; 15 commands + 11 queries on the *same* M9.3 buses;
  6 `cp_billing_*` tables via migration **0016**; 21 API endpoints (60 total, no collisions); and
  a 9-screen Studio at `/studio` — self-contained, stateless, and with **no pricing screen**
  (ADR-0021: the Studio is a Consumer).
  **Versioning:** publishing freezes a self-contained, immutable `CatalogVersion` that
  subscriptions reference by number; historical versions remain readable forever; **rollback
  publishes a new version carrying older content** and records its provenance, so history is
  appended to and never rewritten; catalog activity is append-only and written inside the same
  transaction as the change it describes. Additive throughout — without catalog repositories
  wired, the Application is exactly what M9.3 assembled — and two frozen vocabularies
  (`BillingEventType`, `BillingDiagnosticCode`) gained members while losing none.
  99 tests; repository-wide **2188/2188** green. Indexed in `docs/INDEX.md`; rationale **DL-0116**.

### Changed

- **Three more Epic 9 boundary assertions sharpened, not relaxed (M9.9), and three real
  defects fixed.** The "every controller dispatches through a bus" scan checked *every function*
  in a router file, which a private wire-mapping helper legitimately is not; it now identifies a
  route **structurally** by its `@router.<verb>` decorator — stricter than excluding helpers by
  name, since a new route cannot slip past by being called something the exclusion list allows.
  The plane-wide DELETE allow-list gained two sub-resource deletions (a subscription *item* while
  the agreement is editable, and a *pending intention*), each carrying the reason it removes no
  revenue, plus a new assertion that the agreement itself and every version of it are never
  deletable. The Foundation package-layout test gained the three new subscription modules.
  Three defects were found by the new tests and fixed at the right layer: items supplied at
  subscription creation were stored with the caller's (empty) id, leaving every such line
  un-addressable by `/items/{item_id}` — now minted by the factory, because construction is the
  factory's job; a pricing-only upgrade/downgrade was rejected for "not saying what changes" when
  a target pricing version says exactly that; and an early draft of M9.9 duplicated M9.2's
  `INTERVAL_SECONDS`, which would have given "how long is a month" two answers — removed in favour
  of `PeriodService`. A stale five-option interval dropdown in the M9.7 Pricing Studio (which
  would have produced a 422 against the two-member `BillingInterval`) was corrected at the same
  time.
- **Four more Epic 9 boundary assertions sharpened, not relaxed (M9.8).** The plane-wide
  "no DELETE on revenue records" scan asserted a frozen one-item list of DELETE verbs; M9.8 adds a
  draft-account deletion that provably removes no revenue. The scan now states the rule instead of
  the count: an allow-list where each entry carries the reason it cannot destroy revenue, **plus** a
  new assertion that no DELETE exists on any path touching invoices, ledger, payments, refunds,
  credits, metering, subscriptions, taxes, contracts, licenses, or a published price/catalog
  version. Two migration-chain tests asserted `MIGRATIONS[-1]`, which every future milestone would
  break; both now assert the migration's *position* plus ordering and contiguity, which is the
  actual rule. The Foundation package-layout test gained the three new account modules. One new
  assertion added; none weakened.
- **Three more Epic 9 boundary assertions sharpened, not relaxed (M9.7).** The Application's
  "duplicates no Foundation rule" scan banned the string `Rounding.HALF` outright; a pricing
  *command* legitimately carries a caller's declared rounding policy through to the Foundation as
  a field default. The scan now bans the money-arithmetic calls themselves (`.scaled(`,
  `.allocate(`) and a new AST assertion forbids any `Rounding.` reference **inside a function
  body** — data in transit is allowed, a rounding decision in the wrong layer is not. The catalog
  migration-chain test asserted an exact four-version list; it now asserts the prefix plus
  ordering and contiguity, which is the actual rule (the chain grows, it never reorders). The
  Foundation package-layout test gained the three new pricing modules. Two new assertions were
  added; none was weakened.
- **Eight Epic 9 boundary assertions sharpened, not relaxed (M9.6).** Four searched for a *word*
  the catalog legitimately owns — `CatalogAssembler.rollback` publishes a new catalog version, and
  a `metric.*` string in an OpenAPI example documents a *consumed* event. Those now search for
  transaction machinery (`session.`, `.commit()`, `uow.`) and for emission capability rather than
  for vocabulary. Four asserted frozen counts (`len(__all__) == 203`, exact migration and table
  lists) and now assert **supersets**, which is the actual rule (§A12: surfaces grow additively and
  nothing is ever removed). Two new assertions were added; the suite is stricter after this
  milestone than before it.

- **Billing Control Plane API (M9.5)** — `platform/billing/customer/` (+ spec at
  `docs/architecture/epic9/billing-control-plane-api.md`). The thin HTTP transport for the
  revenue plane: **15 routers / 39 paths / 46 operations** across all 14 bounded contexts plus
  health/ready/version, `create_billing_app()` with lifespan and a DI container over the M9.4
  composition root, Pydantic wire DTOs, centralized error translation, middleware
  (correlation ids, timing, metrics, structured logging, security headers, payload limit, CORS,
  GZip), and versioned OpenAPI with per-route error responses and a bearer scheme.
  **Nothing bypasses the Application** — AST-asserted that every route body reaches a command or
  query bus. Deliberately a **separate ASGI application** from the Epic 8 Control Plane, because
  ADR-0023 gives revenue its own failure domain. Identity is a platform-issued HS256 token
  verified by a stdlib re-implementation (ADR-0031 IR3 forbids `billing`→`api`; interop is proven
  by minting every test token with Epic 8's own encoder); **Billing issues no tokens, exposes no
  auth route, and evaluates no permission** — authorization stays in the Application's
  `BillingAuthorizer` seam and remains **deny-by-default** through production wiring. The
  organization always comes from the verified token, never the request body. Controllers are
  source-asserted to contain no repository access, transaction, pricing, entitlement, or
  authorization logic, and to catch nothing. **Foundation and Application byte-for-byte
  unchanged**; frozen Epics 5–8 untouched; Billing still absent from `platform/bootstrap.py`.
  74 integration tests over the real ASGI app and the real database; repository-wide
  **2089/2089** green. Indexed in `docs/INDEX.md`; rationale **DL-0115**.

### Fixed

- **`SqlPlanRepository.add` raised `IntegrityError` when publishing a plan (M9.4, found at
  M9.5).** `PlanRepository` declares `add` and no `replace`, and the Application legitimately
  calls `add` twice for one plan version (define, then store the published copy). Against the
  in-memory fakes M9.3 tested with the second call overwrote; against the real table it violated
  the catalog's uniqueness constraint, so **the first HTTP request to publish a plan returned
  500**. `add` now upserts on `(org, code, version)`, leaving the Foundation contract and the
  frozen Application untouched.

- **Billing Persistence layer (M9.4)** — `platform/billing/persistence/` (+ spec at
  `docs/architecture/epic9/billing-persistence-layer.md`). The only production implementation of
  the Foundation's **29 repository contracts** and the Application's `UnitOfWork`: 29 SQL-backed
  repositories, `SqlBillingUnitOfWork` (**durable atomicity** replacing M9.3's boundary-only
  abstraction — repository writes and staged application events now commit or vanish together, with
  events published only *after* durable success), a type-driven aggregate↔JSON codec with asserted
  lossless round-trip for all 30 aggregates, a durable `billing.*` fact stream written inside the
  caller's transaction, **30 `cp_billing_*` tables** via migrations **0013–0015** continuing the
  platform's single chain from Epic 8's `0012`, and `build_persistent_billing_application(...)`.
  Storage owns storage only: no business rule, no orchestration, no transport, no provider call.
  Storage-level invariants enforced where a database can guarantee them — append-only
  ledger/credits/usage/tax-evidence/events (**no update or delete method exists to call**), issued
  invoices and published entitlement snapshots immutable, sealing a usage period a one-way door, and
  usage exactly-once at both the repository *and* the unique-constraint layer. **Foundation and
  Application byte-for-byte unchanged and provably unaware persistence exists**; frozen Epics 5–8
  untouched; Billing still absent from `platform/bootstrap.py` (ADR-0031 CR2); deny-by-default
  authorization survives production wiring. 105 integration tests against a real database;
  repository-wide **2012/2012** green. Indexed in `docs/INDEX.md`; rationale **DL-0114**.
- **Billing Application layer (M9.3)** — `platform/billing/application/` (+ spec at
  `docs/architecture/epic9/billing-application-layer.md`). The CQRS orchestration layer over
  the M9.2 Foundation: **26 commands + 19 queries** across all 14 bounded contexts, command
  and query buses with DI and duplicate-registration rejection, a single coverage-asserted
  handler wiring table, command/query use cases, the **UnitOfWork abstraction** (transaction
  boundary + application-event outbox — commit publishes, rollback discards, `__exit__` never
  suppresses; the Foundation is provably unaware transactions exist), **authorization
  orchestration** over an injected `BillingAuthorizer` seam with a **deny-by-default**
  authorizer, 18 view DTOs with pure mappers, and a **disjoint** application-event vocabulary
  (every dispatch audited exactly once; rejections emitted outside the rolled-back
  transaction). **The layer owns no business rules** — validation, construction, lifecycle
  legality, money arithmetic, proration, entitlement composition, quota derivation, invoice
  totals and numbering remain Foundation behaviour, enforced by a structural no-duplication
  test. No persistence, no SQL, no API/HTTP, no Studio, no adapters, no payment or tax
  execution; `BillingApplicationFactory` deliberately has no `.default()`. **1907/1907
  repository-wide tests pass (1855 prior + 52 new, zero regression); ruff clean; the
  Foundation and frozen Epics 5/6/7/8 are byte-for-byte unchanged.** Indexed in
  `docs/INDEX.md`; rationale **DL-0113**.
- **Billing Foundation (M9.2)** — `platform/billing/foundation/` (+ spec at
  `docs/architecture/epic9/billing-foundation.md`). The **first Epic 9 implementation**: the
  pure-domain layer of the revenue plane, 24 modules covering all 14 bounded contexts
  (money · shared domain · accounts · catalog · pricing · subscriptions · entitlements ·
  metering · ledger + credits · invoicing · payments · refunds · tax · contracts +
  licensing) plus lifecycle tables built on `core.fsm`, validation, diagnostics, the
  `billing.*` event vocabulary, 29 repository **interfaces**, stateless services,
  factories, DTOs, and deterministic Clock/Id seams. **Foundation only** — no application
  layer, no persistence implementation, no API/HTTP/FastAPI, no SQL or migrations, no
  Studio, no adapters, no payment/tax providers, no payment execution. Design rules are
  enforced as executable tests: every public aggregate frozen + slotted; transitions return
  new aggregates; `time` imported by exactly one module; an AST test bans
  socket/http/sql/fastapi/pydantic/random/datetime/os from the plane; repositories are
  non-instantiable ABCs; validation never raises while factories fail fast; append-only
  repositories declare no update/delete. ADR-0023 ownership is likewise executable
  (no `metric.*` emitted; usage as a projection keyed by its source event; entitlement ≠
  authorization; mandatory gate classification; integer-minor-unit money; immutable issued
  invoices with gapless numbering; token-only payment instruments; bounded refunds with
  separation of duties; no tax rate table), as are ADR-0031's import rules IR1–IR9.
  **1855/1855 repository-wide tests pass (1774 prior + 81 new, zero regression); `ruff
  check` and `ruff format --check` clean; frozen Epics 5/6/7/8 byte-for-byte unchanged.**
  Indexed in `docs/INDEX.md`; rationale **DL-0112**.
- **ADR-0031 ratified (M9.1A)** — `docs/adr/ADR-0031-billing-plane-repository-placement.md`.
  The **structural placement** record CODING_STANDARDS §2 requires for a new top-level
  directory (ADR-0018 / ADR-0022 precedent), satisfying the Implementation Plan's M9.2
  kickoff entry criterion. Ratifies that the Billing Platform lives permanently at
  **`platform/billing/`** — a self-contained top-level plane, peer to `product/` and
  `actuator/`, importing **`platform/core/` and the standard library only**, with exactly
  one capped read-only exception (`billing/refs.py` → `product`, mirroring
  `actuator/planning.py`). Everything else Billing consumes — organization/seat facts, the
  `metric.*` stream, deployment status, the clock — arrives by **injection through neutral
  ports, never by import**, making the graph acyclic by construction. Fixes: the package
  layout (14 bounded contexts → 14 sub-packages + an `adapters/` tree for the rented
  edges); the layer structure (domain plane + **additive** Epic 8 foundation/application/
  persistence/routers/Studio surface, which imports `billing` and is never imported by it);
  dependency direction (`api → billing`, never the reverse; **no frozen Epic imports
  Billing**); the **composition root** (control plane only — **Billing is never wired into
  `platform/bootstrap.py`**, the placement-level expression of ADR-0023's
  never-blocks-the-runtime guarantee); import rules IR1–IR9 as executable AST assertions;
  a narrow **public surface** (facade, inbound ports, adapter contracts, published read
  contracts) with everything else internal and refactorable without an ADR; extension
  points; testing seams (offline deterministic default, no socket, no money moved); and
  nine repository conventions. Rejects `platform/revenue/` and `platform/commercial/` on
  ubiquitous-language grounds, and nesting under `api/`, `product/`, or `actuator/` as
  freeze-breaking or ownership-inverting. **ADR-0024** stays reserved for Coach and
  **ADR-0025–0030** for the recommended Epic 9 batch. Epic 9 repository topology is now
  frozen — no future placement ADR, no package movement. Registered in
  `docs/adr/README.md` and `docs/INDEX.md`; rationale **DL-0111**.
- **Epic 9 Implementation Plan (M9.1)** — `docs/roadmap/epic9-implementation-plan.md`.
  The last planning milestone before code; **no new architecture and no new ADR** (it
  realizes ADR-0023 + the Blueprint + the Provider Strategy). Delivers a 6-phase plan,
  **7 engineering streams** coordinated by published contracts, **14 bounded contexts** on
  the commercial-lifecycle spine, a dependency graph with three concurrent chains
  (commercial / consumption / adapters) converging at rating, the critical path, a 12-item
  delivery-risk register, a **25-milestone breakdown (M9.2–M9.26)** with per-milestone
  complexity/risk/test-strategy/DoD, a fully offline hermetic testing strategy, a **4-gate
  release strategy** (G1 shadow → G2 design-partner → G3 self-serve GA → G4
  enterprise/marketplace), a 12-line universal Definition of Done, repository impact, and
  10 future extension points. Key scheduling decisions: metering (M9.10) starts at M9.4 in
  parallel with a dedicated reviewer; adapter contracts + deterministic offline fakes land
  at M9.2 before the engines; the entitlement snapshot contract publishes at M9.9 and is
  enforced only at M9.17; **no customer money moves before G1 passes**. Records the
  milestone renumbering superseding Blueprint §27, identifies one structural placement ADR
  as an M9.2 entry criterion, and splits architecture freeze (M9.25) from repository
  release (M9.26). Indexed in `docs/INDEX.md`; rationale **DL-0110**.
- **Billing Build-vs-Buy & Provider Strategy accepted (M9.0C)** —
  `docs/architecture/epic9/billing-provider-strategy.md`. The permanent commercial-provider
  decision, taken before any Epic 9 implementation and moving no ownership boundary:
  **Option C — VoxVoice owns commercial truth; payment and tax providers are rented edges
  behind adapters.** Evaluates Stripe / Paddle / Lemon Squeezy / Chargebee / Recurly /
  custom / hybrid (each as an *owner* and as a *rented edge*) across 22 dimensions.
  **Option B (delegate everything) rejected as unratifiable** — a vendor writing plans,
  pricing, subscriptions, entitlements, or catalog is a second writer (§A2.5, ADR-0023
  §5–§7), and its lock-in is the irreversible kind; **Option A (own everything) rejected**
  on tax-correctness liability, PCI scope, payment-method breadth, and delivery risk.
  Ratifies that providers own **payment movement only** — never subscriptions, plans,
  pricing, entitlements, the commercial catalog, or revenue state — and that Billing stays
  authoritative even while every provider is unavailable. Fixes four adapter families
  (Payment / Tax / Document / Export) under the §A6 capability-plugin pattern with no
  vendor type crossing the seam, mandatory idempotency, verified callbacks, bounded
  retries + circuit breakers, credential-seam secrets, and a **deterministic offline
  adapter as the default outside production**; **multi-provider from day one** with
  deterministic recorded routing; and **merchant-of-record rented per market as a
  distribution channel, never as an owner**. Provider replacement must never require a
  customer migration. Answers the Blueprint's open question and risk **R-12**. This is the
  substance of recommended **ADR-0027**; **ADR-0024 stays reserved for Coach**. Indexed in
  `docs/INDEX.md`; rationale **DL-0109**.
- **ADR-0023 ratified (M9.0B)** — `docs/adr/ADR-0023-billing-sole-owner-of-revenue.md`.
  Ratifies the Billing Platform as the **sole owner of revenue** (plans, pricing, price
  books, commercial catalog, billing accounts, subscriptions, usage records, credits,
  entitlements, discounts/coupons, invoices, payments, refunds, taxes, ledger, contracts,
  licenses, revenue-recognition state) and the owner of **nothing else** — organizations,
  workspaces, projects, agents, knowledge, deployments, runtime, conversation/session
  state, messages, metrics, audit, Product Runtime, Customer Platform, Observability,
  infrastructure, provider execution, authentication, authorization, and RBAC all keep
  their frozen owners. Ratifies that Billing **consumes authoritative facts only**, **never
  measures usage** (`metric.*` read-only), **never blocks Runtime or Deployment**, **never
  performs a permission check** (entitlement ≠ authorization), **publishes immutable
  entitlement snapshots** the owning platform enforces locally (§A2.6, with a mandatory
  fail-open/fail-closed classification per gate), and **never becomes synchronous
  infrastructure**. Fixes `Billing → Platform` as one-way and acyclic (AR-15): **no frozen
  Epic may depend on Billing.** Fulfils the future-owner recommendation in ADR-0021 and
  leaves its Billing-Ledger Forbidden Write unchanged. **ADR-0024 stays reserved for
  Coach**; ADR-0025–0030 are recommended and ratified separately. Registered in
  `docs/adr/README.md` and `docs/INDEX.md`; rationale **DL-0108**.

## Unreleased — Epic 8 (Customer Platform) · 🟡 Design only (M8.0A)

**Product Blueprint & Customer Platform Architecture** — the master, design-only
blueprint for **VoxVoice Studio**, the customer-facing surface over the frozen Runtime
(Epic 5) / Execution (Epic 6) / Product Runtime (Epic 7) stacks. No code, no APIs, no
database tables, and no changes to any frozen Epic (blueprint:
[`docs/architecture/epic8/product-blueprint.md`](docs/architecture/epic8/product-blueprint.md)).

### Added

- **Epic 8 Product Blueprint (M8.0A)** — `docs/architecture/epic8/product-blueprint.md`.
  Delivers all 24 requested artifacts (vision/personas/RBAC/24-module breakdown/customer
  journey/navigation/screen inventory/product hierarchy/SaaS/AI-platform/deployment/
  analytics/billing/security/marketplace/AI Product Manager/UI/competitive analysis/
  roadmap/risks/recommendation). Organizing invariant: **Studio is a Constitution-§A0
  Consumer + a management/authoring surface that owns no runtime state** — every module
  is traced to the frozen capability that owns its data. Names the one honest gap (a new
  additive **Deployment Actuator** is required because Epic 7 froze at deployment
  *intent*). Recommends — does not ratify — **ADR-0021** (Studio-owns-no-state) plus
  ADR-0022/0023/0024. Indexed in `docs/INDEX.md`; rationale **DL-0086**.
- **ADR-0021 ratified (M8.0B)** — `docs/adr/ADR-0021-studio-consumer-and-management-surface.md`.
  Ratifies the Studio ownership model: **VoxVoice Studio is a Consumer + Management
  Surface that owns no runtime state** — it owns only UI, user workflows, the
  authoring/management experience, orchestration/deploy *requests*, presentation,
  projections, and navigation; it never mutates execution/conversation/memory/knowledge/
  deployment/session/provider state, the billing ledger, or the event stream. Fixes the
  `Studio → Platform`, never `Platform → Studio` dependency direction (acyclic, AR-15),
  an Ownership Matrix mapping every Blueprint module to its authoritative owner, and the
  Allowed/Forbidden Writes lists. Mandatory on every future Epic 8 milestone; any
  deviation requires a superseding ADR. Verified consistent with the Constitution
  (§A2.5 one-writer-per-fact) and ADR-0018/0019/0020 (no conflict). Register/INDEX
  updated; cross-referenced to the Blueprint. Rationale **DL-0087**.
- **ADR-0022 ratified (M8.0C)** — `docs/adr/ADR-0022-deployment-actuator-runtime-actuation.md`.
  Defines the **Deployment Actuator**: a new component (a new top-level `platform/actuator/`
  package, peer of `product/`) that is the **sole owner of deployment execution**. It
  consumes the frozen Epic 7 `DeploymentPlan` **read-only**, drives real infrastructure
  only through pluggable **Infrastructure Adapters** (§A6 pattern — Kubernetes/Docker/
  GitOps/ECS/Azure/GCP/Nomad each = one adapter + one registry entry, additive; a Local
  adapter is the deterministic offline default), and owns execution/orchestration/runtime
  deployment state/lifecycle FSM/retries/rollback/approvals — while **Product Runtime stays
  frozen and purely declarative**. Deterministic behaviour over a non-deterministic infra
  edge (§A2.4); one-writer-per-fact for deployment state (§A2.5); full §A10 failure ladder
  (retry/rollback/partial/timeout/idempotency/cancellation — no orphaned deploys); §A11.2
  org-scoped RBAC + separation-of-duties approvals + audit; `deployment.*` events on the
  §A5 envelope. Dependency direction frozen `actuator → product` (read-only), never
  reverse (acyclic, AR-15). Verified consistent with the Constitution and
  ADR-0018/0019/0020/0021 (no conflict; realizes the "future Deployment Actuator" ADR-0021's
  Ownership Matrix already named). Register/INDEX updated; cross-referenced to the Epic 7
  Freeze, the Blueprint, and ADR-0021. Rationale **DL-0088**.
- **Epic 8 Implementation Plan (M8.1)** — `docs/roadmap/epic8-implementation-plan.md`. The
  final planning milestone before implementation: converts the Product Blueprint into an
  executable roadmap with **no new architecture and no new ADR** (realizes ADR-0021/0022).
  Delivers a 9-phase plan (M8.2 Foundation → … → M8.10 Marketplace) on an activation spine
  (signup→org→workspace→agent→prompt→test→deploy→monitor); 7 parallel engineering streams
  coordinated by OpenAPI + event-schema contracts (the S3 Deployment Actuator + Local
  adapter is isolated and parallelizable from day 1); 8 bounded contexts; aggregate & API-
  domain identification (no tables/endpoints); a Must/Should/Future UI map; a dependency
  graph; a per-milestone breakdown (complexity/risk/test/DoD); the MVP (spine =
  M8.2/8.3/8.4/8.6/8.7/8.8); a 4-gate release strategy (Alpha→Private Beta→Public Beta→GA);
  and a 12-item risk register. Names the one governance action: a `studio/` placement ADR
  as an M8.2 kickoff entry-criterion (CODING_STANDARDS §2). Indexed in `docs/INDEX.md`;
  rationale **DL-0089**. **Implementation begins at M8.2 on acceptance.**

### Added

- **Customer Platform Foundation (M8.2)** — `platform/api/foundation/`, the first Epic 8
  implementation and the base every later Epic 8 feature builds on. A **subpackage of the
  Control Plane** (not a new top-level package, so no placement ADR is required — the
  M8.1-flagged obligation applies only to `studio/`), with a pure domain layer:
  `api.foundation → core, tenant` only, importing no FastAPI and no frozen runtime
  (test-enforced, acyclic). Ships **Identity** (User/Organization/Workspace/Membership/
  RoleBinding), **Platform** (Environment/Project/UserPreferences), **Security** (11-role
  RBAC per ADR-0021 §5, a pure deterministic `PermissionEvaluator`, org/workspace scoping,
  `OrganizationIsolationError` enforcement, and an `RbacAuthorizer` implementing the frozen
  `tenant.access.Authorizer` seam), **Configuration** (settings + deterministic
  `FeatureFlagResolver`), and **Infrastructure** (repository interfaces + in-memory impls,
  six domain services, typed `FoundationEvent` audit events, validation, DTOs, a
  composition `FoundationFactory`). `create_organization` auto-provisions the owner;
  `create_workspace` auto-provisions a default `dev` environment; denied actions are
  audited, never silent. Determinism via injected `Clock`/`IdGenerator` seams (§A2.4).
  Component spec: [`docs/architecture/epic8/customer-platform-foundation.md`](docs/architecture/epic8/customer-platform-foundation.md).
  17 tests; **1573/1573 repository-wide; ruff clean; zero Epic 5/6/7 files modified.**
  Out of scope (later milestones): Agents/Prompts/Knowledge/Testing/Deployment/Billing/
  Marketplace/Coach/Analytics/Sessions + the FastAPI router layer + a durable SoR backing.
  Rationale **DL-0090**.
- **Customer Platform Application Layer (M8.3)** — `platform/api/application/`, the CQRS
  orchestration layer between a future API and the M8.2 Foundation. `api.application →
  api.foundation, core, tenant` only (no FastAPI/HTTP/WebSocket/DB/frozen-runtime —
  test-enforced; the API/transport layer does not exist yet). Ships **commands**, **queries**,
  **use cases** (authorize→transaction→Foundation→map→event, no domain-logic duplication),
  **CommandBus/QueryBus** with DI + thin handlers, a **Unit of Work** transaction abstraction
  (application-event outbox — publish on commit, discard on rollback), **authorization
  orchestration** via the Foundation `Authorizer` seam (no permission logic upward),
  **cross-cutting audit** (`use_case.completed`/`use_case.rejected`, rejection surviving
  rollback), **DTO mapping**, and **application events disjoint from the Foundation domain
  vocabulary** (no duplication). Deferred honestly (Foundation unchanged): `UpdateOrganization`
  (absent) and hard member delete (`RemoveMember` → `SUSPENDED` soft-remove). Component spec:
  [`docs/architecture/epic8/customer-platform-application-layer.md`](docs/architecture/epic8/customer-platform-application-layer.md).
  13 tests; **1586/1586 repository-wide; ruff clean; M8.2 Foundation and Epics 5/6/7
  byte-for-byte unchanged.** Rationale **DL-0091**.
- **Customer Platform Persistence Layer (M8.4)** — `platform/api/persistence/`, the only
  production implementation of the M8.2 Foundation repository interfaces and the M8.3
  Application `UnitOfWork` abstraction, swapped in at a composition root with **no interface
  change**. SQLModel-backed (`cp_*` tables). Ships 8 `Sql*Repository` implementations (org-
  scoped reads, row↔frozen-domain mapping, never commit), a production **`SqlUnitOfWork`**
  giving **real atomic rollback of DB writes** (closing the M8.3 in-memory limitation — data
  and events now roll back together), pure row↔domain **mapping** (no persistence leakage),
  org-scoped **query helpers**, a declarative **migration structure** (`migrations/`, no app
  logic), and a **composition root** (`build_persistent_application`) wiring SQL repos + Unit
  of Work into the Foundation/Application via their existing seams. `api.persistence →
  api.foundation, api.application, core, tenant, sqlmodel` only — no FastAPI/HTTP/frozen-
  runtime; neither Foundation nor Application imports persistence (AST-verified). Component
  spec: [`docs/architecture/epic8/customer-platform-persistence-layer.md`](docs/architecture/epic8/customer-platform-persistence-layer.md).
  9 persistence-backed integration tests (existing in-memory unit tests kept); **1595/1595
  repository-wide; ruff clean; Foundation, Application, and Epics 5/6/7 byte-for-byte
  unchanged.** Rationale **DL-0092**.
- **Customer Platform Control Plane API (M8.5)** — `platform/api/customer/`, a **thin**
  FastAPI transport boundary over the M8.3 Application buses wired to M8.4 persistence, with
  its own `create_app()` (the legacy `api/main.py` app is untouched). Owns transport only:
  HTTP parse/serialize, dependency injection (`Container` + FastAPI providers, replaceable
  for tests), authentication (dependency-free HS256 **bearer/JWT identity resolution — no
  authorization**), `RequestContext`, centralized **error translation** (422+diagnostics /
  403 / 404 / 409 / 401 / 500, uniform envelope), **middleware** (correlation ids / timing /
  metrics / structured logs / security headers / GZip / CORS), **health** (`/health`,`/ready`
  with DB `SELECT 1`,`/version` — no runtime checks), auto **OpenAPI** (tags/examples/error-
  responses/bearer scheme, v1.0.0), and `/v1` versioning. Thin domain routers (auth/orgs/
  workspaces/members/projects/environments/preferences/system) dispatch Application commands/
  queries; controllers own **no** business logic/repository/transaction/permission-evaluation
  (test-asserted); authorization forwarded to the Application; no domain object exposed
  directly. `api.customer → api.application, api.persistence, api.foundation, core, tenant,
  fastapi/pydantic`; lower layers never import the API (AST-verified). Component spec + API/
  OpenAPI docs: [`docs/architecture/epic8/customer-platform-control-plane-api.md`](docs/architecture/epic8/customer-platform-control-plane-api.md).
  15 API integration tests; **1610/1610 repository-wide; ruff clean; Foundation, Application,
  Persistence, and Epics 5/6/7 byte-for-byte unchanged.** Rationale **DL-0093**.
- **Organization Lifecycle vertical slice (M8.6)** — the first complete customer-facing
  feature, end-to-end across all Epic 8 layers, delivered as **additive, non-breaking
  extensions** (no new ADR, no redesign; existing contracts + all 1610 prior tests
  preserved; frozen Epics 5/6/7 untouched). Adds organization settings/rename/logo/timezone/
  language/metadata, soft-delete + status, activity summary + feed, membership tiers
  (Owner/Admin/Member/Viewer via additive `ORG_ADMIN/ORG_MEMBER/ORG_VIEWER` roles; no custom
  roles), and the full invitation lifecycle (invite/accept/decline/resend/cancel/expiry +
  audit) modelled as a pending membership. Layers extended additively: Foundation
  (`org_lifecycle.py` services + domain fields/enums/rbac/events/`OrganizationRepository.update`),
  Application (`org_lifecycle.py` commands/queries/use cases registered into the existing
  buses), Persistence (additive `cp_*` columns + `SqlOrganizationRepository.update` +
  migration `0002`), Control Plane API (thin `PATCH/DELETE /v1/organizations/{id}`,
  `.../activity`, `.../invitations` routers). Plus a self-contained **Studio UI** served at
  `/studio` (dashboard/settings/members/invite/pending/activity; responsive; loading/empty/
  error states) — served inside `api/customer/studio/` (subpackage; no new-top-level-dir
  placement ADR needed). Component/API/Studio spec:
  [`docs/architecture/epic8/organization-lifecycle.md`](docs/architecture/epic8/organization-lifecycle.md).
  9 tests incl. the full Definition-of-Done flow, authz failure, cross-org isolation,
  invitation expiry, soft-delete, Studio served. **1619/1619 repository-wide; ruff clean;
  frozen Epics 5/6/7 untouched.** Rationale **DL-0094**.
- **Workspace Lifecycle vertical slice (M8.7)** — the second complete customer-facing
  feature, end-to-end across all Epic 8 layers, delivered as **additive, non-breaking
  extensions** (no new ADR, no redesign; existing contracts + all 1619 prior tests preserved;
  frozen Epics 5/6/7 untouched). Adds workspace rename/settings (description/icon/color/
  metadata), soft-archive + restore + status, activity summary + feed (filtered to the
  workspace), workspace membership (Admin/Member/Viewer via additive `WS_MEMBER/WS_VIEWER`
  roles + reused `WORKSPACE_ADMIN`; no custom roles) with workspace-scoped role bindings, and
  navigation (switcher/recent/favorite/default/search) backed by the existing `UserPreferences`.
  Layers extended additively: Foundation (`workspace_lifecycle.py` services + domain fields/
  enums/rbac/events/`WorkspaceRepository.update` + new `WorkspaceMembershipRepository`),
  Application (`workspace_lifecycle.py` commands/queries/use cases registered into the existing
  buses), Persistence (additive `cp_workspace` columns + new `cp_workspace_membership` table +
  `SqlWorkspaceMembershipRepository` + migration `0003`), Control Plane API (thin
  `PATCH …/workspaces/{id}`, `.../archive`, `.../restore`, `.../activity`, `.../search`,
  workspace `.../members`, and `workspace-navigation` routers). Plus the **Studio UI** Workspaces
  section (list/switcher with favorite/default + search, create, dashboard/settings, archive/
  restore, members, activity) added to the existing `api/customer/studio/` bundle. Only ACTIVE
  org members may join a workspace (org+workspace isolation); workspace name uniqueness enforced
  on rename. Component/API/Studio spec:
  [`docs/architecture/epic8/workspace-lifecycle.md`](docs/architecture/epic8/workspace-lifecycle.md).
  11 tests incl. the full Definition-of-Done flow, authz failure, cross-org + cross-workspace
  isolation, archive-hides-from-switcher/search, Studio served. **1630/1630 repository-wide;
  ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0095**.
- **Project Lifecycle vertical slice (M8.8)** — the third complete customer-facing feature,
  end-to-end across all Epic 8 layers, delivered **reuse-first** with a **minimal additive
  Foundation change** (no new ADR, no redesign; existing contracts + all 1630 prior tests
  preserved; frozen Epics 5/6/7 untouched). Adds project rename/settings (description/icon/
  metadata), soft-archive + restore + status, activity summary + feed (filtered to the
  project), project membership (Admin/Member/Viewer), environment management (fixed dev/
  staging/prod set + per-user default/switch, **no deployment**), and search. Reuse decisions:
  project **creation** stays on the frozen M8.2 service/M8.5 route; **no new RBAC scope** —
  project ops authorize via the existing workspace-scoped `PROJECT_MANAGE` (`rbac.py`
  unchanged; PROJECT-granular enforcement deferred, flagged honestly); **environments reuse
  `UserPreferences` + `EnvironmentKind`** (no new persistence). Layers: Foundation
  (`project_lifecycle.py` services + additive `Project` fields/enums/events/`ProjectRepository.get`/
  `update` + new `ProjectMembershipRepository`; `services.py`/`rbac.py` untouched), Application
  (`project_lifecycle.py` commands/queries/use cases), Persistence (additive `cp_project`
  columns + new `cp_project_membership` table + `SqlProjectMembershipRepository` + migration
  `0004`), Control Plane API (thin `PATCH …/projects/{id}`, `.../archive`, `.../restore`,
  `.../activity`, `.../search`, project `.../members`, `.../environment` routers). Plus the
  **Studio UI** Projects section (list/search, create, dashboard/settings, archive/restore,
  environment selector, members, activity) added to the existing `api/customer/studio/` bundle.
  Only ACTIVE org members may join a project (org+workspace+project isolation); project name
  uniqueness enforced on rename. Component/API/Studio spec:
  [`docs/architecture/epic8/project-lifecycle.md`](docs/architecture/epic8/project-lifecycle.md).
  11 tests incl. the full Definition-of-Done flow, authz failure, cross-org/cross-workspace/
  cross-project isolation, archive-hides-from-search, Studio served. **1641/1641
  repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0096**.
- **Epic 8 Architecture Freeze — Customer Platform v1.0 (M8.20)** — governance milestone; **no
  implementation, no redesign, no new features, no ADR**. Freezes the Epic 8 Customer Platform
  **architecture** (M8.2–M8.18): its public interfaces, ownership boundaries (single writer per
  fact), one-way acyclic dependency direction (`Studio→API→Application→Foundation→{Persistence,
  adapters}`; `actuator→product` read-only; no Epic 8 layer imports a frozen engine and no frozen
  Epic imports Epic 8 — AST-verified), extension points, and the determinism/identity/config/
  provider/runtime/deployment/knowledge/audit/observability/security/operational rules. Freeze
  manifest: [`docs/releases/epic8-customer-platform-v1.0.md`](docs/releases/epic8-customer-platform-v1.0.md);
  cross-references the Epic 5/6/7 freeze manifests. Extension policy: additive extensions permitted;
  a breaking change requires a new ADR + major version + new baseline. Repository audit at freeze:
  **1774/1774 tests; ~218 Epic-8 capability tests; ruff clean; migrations 0001–0012 valid; frozen
  Epics 5/6/7 byte-for-byte unchanged.** **Governance status recorded honestly (manifest §22): the
  Epic 8 implementation is present in the working tree but NOT committed to version control (the
  unresolved M8.19 MEDIUM condition). This is an architecture freeze only — no commit, push, tag, or
  release is performed or claimed.** Verdict: 🔒 **EPIC 8 ARCHITECTURE FROZEN (Customer Platform
  v1.0)**. Rationale DL-0106.
- **Production Readiness, Hardening & GA Certification (M8.18)** — hardening + certification **only**
  (no architectural redesign, no new features, no new domains/aggregates/customer-APIs, no new ADR;
  frozen Epics 5/6/7 unchanged). **Production hardening** (all behind existing seams): real
  `HttpTransport` (stdlib `urllib`, connection pooling, per-request timeout, typed retry/non-retry
  errors) + a `CredentialProvider` secret seam (`EnvCredentialProvider`/`ChainedCredentialProvider`
  key-rotation) + `redact`/`redact_headers` — Echo/Env remain the deterministic offline default;
  configuration validation that **fails fast in production** (rejects the default JWT secret,
  wildcard CORS, and in-memory SQLite); startup dependency verification + graceful shutdown; rate
  limiting (429 + `Retry-After`) + payload limits (413) + a fuller security-header set (HSTS over TLS
  only, `Permissions-Policy`, `X-XSS-Protection`, `Cache-Control: no-store`, …); production JWT
  validation + a **key-rotation** seam (`verification_secrets` + `decode_any`, wired into auth); 5xx
  **error redaction** + structured, correlated logging. **Verification** — 27 new tests:
  `test_production_hardening` (13), `test_ga_certification` (10 — migration chain **0001…0012**
  contiguous/ordered/idempotent, OpenAPI completeness, SemVer + backward-compat, **dependency
  boundaries + one-way frozen-Epic freeze**, determinism, append-only audit), `test_scale` (4 —
  5 000-event audit history, 100-project org, 200-chunk knowledge base, 30-turn soak, 20
  deployments). **Documentation** — 13 operational documents (Production Architecture, Operations,
  Deployment, Troubleshooting, Scaling, Security, Backup & Recovery, Disaster Recovery, Runbook,
  Support Handbook, Production Checklist, GA Checklist) + the formal **GA Certification v1.0**
  (`docs/releases/ga-certification-v1.0.md`) verifying every ADR, Constitution rule, ownership
  boundary, dependency direction, and frozen Epic. **1774/1774 repository-wide; ruff clean; frozen
  Epics 5/6/7 byte-for-byte unchanged.** Verdict: 🟢 **GA v1.0 — CERTIFIED PRODUCTION-READY**.
  Rationale **DL-0106**.
- **Observability Platform (M8.17)** — a **passive read-model** over the durable Audit stream
  (M8.9): metrics / logs / traces / health / usage / cost / dashboards are *projections* computed on
  read from the events the platform already emits; **only alert rules are durable config**. **Golden
  rules:** Runtime owns execution, Deployment owns deployment, Knowledge owns retrieval, Tool
  Framework owns tools, **Observability owns observation** — no monitoring logic entered any of those
  layers (all reused exactly and unchanged); no new ADR, no redesign; frozen Epics 5/6/7 untouched;
  all 1733 prior tests preserved. Ships: **metrics** (`MetricsRegistry`/`MetricsCollector`/
  `MetricsService` + a 15-metric event-derivation catalog); **structured logs** (each audit record is
  one immutable `LogEntry` — the audit store *is* the log store; level/category derived); **distributed
  traces** (`Trace`/`Span` — correlated events grouped into spans, **no OpenTelemetry**); **alerting**
  (`AlertRule`/`AlertEvaluator`/`AlertManager` — comparators + windows, evaluated **on demand**, **no
  notifications, no poller**); **health** (`HealthMonitor` — success/failure ratios + consumed
  Deployment/Runtime health, never probes); **usage** + **estimated cost** (rate table, **not
  billing**) + **performance**; the eight dashboard read models (org/workspace/project/agent/
  deployment/knowledge/provider/system). **No polling, no direct Runtime/Deployment access, no
  duplicate audit events.** Foundation (additive): `observability_domain.py` + `observability.py`
  (all services over the injected `AuditReader`) + `AlertRuleRepository` + `FoundationEventType.
  observability.*` (alert-config facts only) + id prefix `alr`; the trace hash uses `hashlib` (no
  `product` import — boundary tests green). Persistence: `cp_alert_rule` (the **only** Observability
  table) + repo + **migration `0012`**; metrics/logs/traces/health add no tables. Application:
  `observability.py`. API: org-scoped `…/observability/…` router. Studio: Metrics/Trace/Log/Health/
  Usage/Cost/Alert/Provider/Knowledge/Deployment screens. 14 tests (metric/log/trace/health/usage/
  cost/perf/alert + API e2e **DoD** + audit-non-duplication + authorization + isolation + Studio).
  **1747/1747 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0105**; spec
  `docs/architecture/epic8/observability-platform.md`.
- **Deployment Actuator (M8.16)** — the **Deployment Actuator** (ADR-0022): the sole owner of
  deployment **execution**, a new top-level `actuator/` package above the frozen Product Runtime,
  consuming its `DeploymentPlan` **read-only** (Product Runtime is not modified and never learns the
  Actuator exists). **Golden rules:** Deployment owns execution, Product Runtime owns planning,
  Runtime owns conversations, Studio owns interaction, Infrastructure owns infrastructure. No new
  ADR, no redesign; Product Runtime / Conversation Runtime / Tool Framework / Provider Binding /
  Knowledge Tool reused exactly and unchanged; frozen Epics 5/6/7 untouched; all 1717 prior tests
  preserved. Ships: the deterministic lifecycle **FSM** (`REQUESTED→VALIDATING→PENDING_APPROVAL→
  QUEUED→EXECUTING⇄VERIFYING→COMPLETED`, + `FAILED`/`ROLLING_BACK→ROLLED_BACK`/`CANCELLED`); the
  `DeploymentEngine` (**sole writer**) + 9 coordinators (queue/scheduler/approval/rollback/health/
  cancellation/retry/timeout/executor) + `DeploymentActuator` facade; provider-independent
  **Infrastructure Adapters** — **Local executes** deterministically offline, Docker/Kubernetes/
  GitOps/Terraform/AWS-ECS/Azure/GCP/Nomad **declared** with capability flags (raise on actuate);
  **approval** (separation of duties — requester ≠ approver, production auto-gated); first-class
  **rollback** (manual + health-failed auto-rollback); **health verification** (startup→readiness→
  liveness); durable execution history. `actuator/planning.py` is the **only** place Epic 7's
  `DeploymentPlan` is reused (read-only), so the **Foundation imports only `actuator`, never
  `product`** (frozen-Epic import boundary preserved). Foundation (additive): `deployment.py`
  `DeploymentActuatorService` (authorizes via existing `PROJECT_MANAGE`; mirrors `deployment.*` into
  the durable Audit Platform) + `FoundationEventType.DEPLOYMENT_*` + a defaulted execution repo.
  Persistence: `cp_deployment_execution` + repo + **migration `0011`** (aggregate as JSON).
  Application: `deployment.py` submit/approve/reject/cancel/retry/rollback + plan/status/history/
  timeline/logs/health/targets. API: thin `…/deployments/…` router. Studio: Deployment Dashboard/
  Environment Selector/History/Approval Queue/Rollback Manager/Health Viewer/Timeline/Execution
  Logs. 16 tests (FSM + adapters + engine lifecycle incl. health-failed auto-rollback + API e2e
  **DoD** + SoD + audit + isolation + Studio). **1733/1733 repository-wide; ruff clean; frozen Epics
  5/6/7 untouched.** Rationale **DL-0104**; spec `docs/architecture/epic8/deployment-actuator.md`.
- **Knowledge Tool / RAG Foundation (M8.15)** — Knowledge implemented **strictly as a Tool
  Framework capability**. **Knowledge is NOT part of Runtime / LLM-adapter / Memory** — a
  `knowledge`-category tool is invoked by the existing Tool Executor (M8.14) and reaches the
  Runtime **only as a Tool Result (Context Package)**. **Golden rules:** Knowledge owns retrieval,
  Runtime owns orchestration, LLM owns generation, Adapters own execution; **no retrieval logic in
  the Runtime**. Delivered **reuse-first** with an additive Foundation change (no new ADR, no
  redesign; existing contracts + all 1708 prior tests preserved; Runtime/Tool-Framework/Provider-
  Binding/Playground reused exactly and unchanged; frozen Epics 5/6/7 untouched). Ships: assets
  (collection→document→chunk + metadata/version/status/soft-delete/archive/restore); document
  pipeline (upload/replace/delete/archive/restore/version + checksum + duplicate + MIME + max-size,
  **no OCR/image**); **deterministic chunking** (fixed-size + overlap + checksum); **embedding
  providers** (OpenAI/Gemini/Voyage/Cohere/Local); **vector stores** (FAISS/Qdrant/PgVector/
  Pinecone/Chroma); the retrieval pipeline (query→embedding→search→ranking→filtering→selection→
  context package, **no LLM reranking**); the **Knowledge Tool** executor bound into the M8.14
  engine. Embedding/vector providers live in `api.adapters` behind provider-independent seams over
  the injectable transport — **deterministic offline** (hash embedder + cosine), real providers in
  production; **no provider SDK, no frozen-Epic import** (boundary tests green). Foundation
  (additive): `knowledge_domain.py` + `knowledge_chunking.py` + `knowledge_adapters.py` +
  `knowledge.py` + repos/DTOs/events (existing services + M8.11–M8.14 untouched). Persistence: 3
  tables + repos + **migration `0010`** (chunk vectors as JSON). Application: `knowledge.py`. API:
  thin `…/knowledge/…` router. Studio: Knowledge Dashboard/Collections/Documents/Upload/Chunk/
  Metadata/Vector Status/Retrieval Inspector/Search Playground. 9 tests (chunking + 5 embedders +
  5 stores + pipeline + retriever + API e2e DoD incl. **retrieval-through-Tool-execution** + audit
  + isolation + Studio). **1717/1717 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.**
  Rationale **DL-0103**; spec `docs/architecture/epic8/knowledge-tool.md`.
- **Provider Adapter Binding (M8.14)** — binds the Conversation Runtime to **real execution
  providers** via the existing M8.12 adapter interfaces, in a new **`api.adapters` layer**.
  **Golden rules:** Runtime owns orchestration (unchanged — still only plans); Providers own
  execution; Adapters own translation; **no provider-specific logic in the Runtime**. Delivered
  **reuse-first** with an additive Foundation **seam** (no new ADR, no redesign; existing contracts
  + all 1698 prior tests preserved; frozen Epics 5/6/7 untouched). Ships production **LLM adapters**
  (Anthropic/OpenAI/Gemini/Groq/Azure — real request-shaping + response/token-usage parsing),
  **voice adapters** (Deepgram/ElevenLabs/Cartesia/OpenAI, metadata-only), **tool executors**
  (HTTP/Function/Webhook/MCP/Database/Custom respecting ordering/enable/override/deps/timeout/
  retry), a durable **memory adapter** (message store — **no vectors/RAG**), a
  `ProviderExecutionEngine` (execute + **streaming** with token/tool/voice events + cancellation +
  backpressure), and **resilience** (retry/timeout/circuit-breaker/rate-limit/graceful-degradation).
  **The network egress is an injectable `Transport` seam** — deterministic `EchoTransport` offline
  in tests (running the real translation code), HTTP in production; **no provider SDK, no
  frozen-Epic import** (boundary tests green). Foundation (additive seam): `execution_domain.py` +
  `execution.py` (`ExecutionEngine` ABC + offline `MockExecutionEngine` + `ConversationExecutionService`)
  + message repo + DTOs/events (`services.py` + M8.12 runtime + M8.13 playground untouched).
  Persistence: `cp_conversation_message` + repo + **migration `0009`**; composition threads an
  `engine_factory` (provider-agnostic). Application: `execution.py` `ExecuteTurn`(+stream)/messages/
  provider-diagnostics. API: additive `…/conversations/{id}/execute|stream|messages|provider`; the
  **customer Container** injects the real engine. Studio: Streaming View / Provider Inspector /
  Token Usage / Latency / Retry+Execution Timeline / Provider Diagnostics. 10 tests (5 LLM + 4 voice
  + 6 tool + resilience + engine/streaming + API e2e DoD + audit + isolation + determinism + Studio).
  **1708/1708 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0102**;
  spec `docs/architecture/epic8/provider-adapter-binding.md`.
- **Conversation Playground (M8.13)** — a developer **playground** for inspecting/debugging/
  validating Conversation Runtime behaviour through **mock adapters only**. **A testing
  environment, NOT production deployment. No external providers, no real LLM API, no real tool
  execution, no voice synthesis, no network.** Delivered **reuse-first** with an **additive
  Foundation change** and **no new persistence** (no new ADR, no redesign; existing contracts +
  all 1687 prior tests preserved; frozen Epics 5/6/7 untouched). Deterministic **mock adapters**
  (`MockLLMAdapter` echoes prompt metadata + token/timing estimates; `MockToolExecutor` returns
  deterministic fake outputs; `MockVoiceAdapter` returns metadata only, `audio=None`;
  `MockMemoryAdapter` stateless stub) implement the M8.12 runtime adapter interfaces — identical
  inputs → byte-identical outputs. Conversation flow: **start** (reuses runtime create-session —
  **draft never runs**), **continue** (a turn runs the mocks over the resolved plan + appends a
  `ConversationTurn`), **reset** (clears turns, keeps the plan), **close**. **Reuses the existing
  `ConversationSession`** — turns live in `cp_conversation_session.state` JSON, so **no new table/
  migration**. Prompt/variable/tool-plan inspection **reuses the M8.12 runtime queries**; adds
  adapter-output/timeline/diagnostics views. The playground **never mutates** the published config
  or resolved plan; **replaying a conversation yields identical turns**. No new RBAC scope
  (workspace-scoped `PROJECT_MANAGE`); audit reuses M8.9. Foundation (additive): `runtime_mocks.py`
  + `playground_domain.py` + `playground.py` + DTOs/events (`services.py` + M8.12 runtime
  untouched). Application: `playground.py` commands/queries/use cases. API: thin
  `…/agents/{id}/playground/conversations` router. Studio: Conversation Playground / Chat Window /
  Prompt Inspector / Variable Inspector / Execution Timeline / Tool Invocation Viewer / Adapter
  Output Viewer / Diagnostics Panel / Session Browser. 11 tests (deterministic mock adapters +
  Foundation unit incl. draft-blocked/replay-determinism/no-mutation + API e2e incl. full
  Definition-of-Done, audit reuse, isolation, Studio served). **1698/1698 repository-wide; ruff
  clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0101**; spec
  `docs/architecture/epic8/conversation-playground.md`.
- **Conversation Runtime (M8.12)** — the **execution/orchestration plane** that runs a
  **Published Agent Version** to a deterministic **execution plan**. **No LLM, no tool execution,
  no voice, no real provider, no message history, no vectors, no deployment, no playground** —
  only orchestration. Delivered **reuse-first** with an **additive Foundation change** (no new
  ADR, no redesign; existing contracts + all 1676 prior tests preserved; frozen Epics 5/6/7
  untouched). **Golden rules (tested):** only a **published** version runs (a draft raises); the
  runtime **never mutates** config or history; it is **deterministic**; it is stateless except the
  explicit persisted `ConversationState`. Pipeline: load published version → **resolve variables**
  (precedence System < Project < Agent < Runtime, immutable) → **build prompt** (deterministic,
  secrets masked `***`) → **resolve tool registry** (M8.11 ordering/enable/override/deps → an
  executable plan, **not executed**) → **execution plan** + required **adapter interfaces**.
  **Adapter interfaces only** — `LLMAdapter`/`VoiceAdapter`/`ToolExecutor`/`MemoryAdapter` ABCs
  (no implementations, no SDKs) are the seam the frozen Epic 5/6 runtime binds later; the control
  plane **never imports the frozen runtime** (dependency-boundary test green). Session lifecycle:
  create/resume/close/expire + correlation ids + metadata. Execution result: resolved prompt/
  variables/tool plan/timing/diagnostics — **no model response**. No new RBAC scope (workspace-
  scoped `PROJECT_MANAGE`); audit reuses M8.9. Foundation (additive): `runtime_domain.py` +
  `runtime_adapters.py` + `runtime.py` + repo/DTOs/events/prefix (`services.py` untouched).
  Application: `runtime.py` commands/queries/use cases. Persistence: `cp_conversation_session`
  table (session + state JSON; **no message history/vectors**) + SQL repo + **migration `0008`**.
  API: thin `…/agents/{id}/sessions` router. Studio: Runtime Inspector / Execution Plan / Session /
  Variable Inspector / Tool Plan / Diagnostics (no chat). 11 tests (Foundation unit incl. draft-
  blocked/precedence/secret-mask/determinism/no-mutation + API/persistence e2e incl. full
  Definition-of-Done, audit reuse, **resume across restart**, isolation, Studio served). **1687/1687
  repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0100**; spec
  `docs/architecture/epic8/conversation-runtime.md`.
- **Tool Framework vertical slice (M8.11)** — a **provider-independent Tool Framework** that
  lets published Agents declare, configure and validate tools. **No tool execution, no LLM, no
  RAG, no deployment, no external calls** — declaration only; Conversation Runtime consumes it
  next. Delivered **reuse-first** with an **additive Foundation change** (no new ADR, no
  redesign; existing contracts + all 1666 prior tests preserved; frozen Epics 5/6/7 untouched).
  A **Tool** is a configuration object owned by one Project, reusable across its agents. Ships:
  **tool catalog** + registration + **typed-data configuration** (config/input/output schemas +
  settings + timeout + retry policy + depends_on — plain dicts/values, **never an SDK object or
  provider client**), **enable/disable**, **ordering**, **validation** (duplicate-name, category,
  typed schemas, **circular references** via a `depends_on` DFS, project/workspace/org isolation),
  **version** (monotonic, bumped on config change), and **agent integration** (attach/detach/
  enable/disable/**reorder**/configure, project-isolated, per-attachment enable + config
  override). **Tool execution is abstracted behind interfaces** — there is no execute path; the
  framework knows nothing about any LLM. History reuses the durable M8.9 Audit Platform. No new
  RBAC scope (workspace-scoped `PROJECT_MANAGE`/`PROJECT_READ`); CQRS/UoW/persistence seams
  unchanged. Foundation (additive): `tool_domain.py` + `tool_framework.py` + repos/DTOs/events/
  prefixes/diagnostics (`services.py` untouched). Application: `tool_framework.py` commands/
  queries/use cases into the existing buses. Persistence: `cp_tool` + `cp_agent_tool` tables +
  SQL repos + **migration `0007`**. API: thin `…/projects/{id}/tools` + `…/agents/{id}/tools`
  routers. Studio: Tool Catalog/Library/Details/Configuration + Agent Tool Assignment/Ordering.
  10 tests (Foundation unit + API/persistence e2e incl. full Definition-of-Done, circular-ref,
  attach/reorder, restart durability, authz failure, cross-project/cross-org isolation, Studio
  served). **1676/1676 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale
  **DL-0099**; spec `docs/architecture/epic8/tool-framework.md`.
- **Agent Builder vertical slice (M8.10)** — the **first AI-native customer feature**: design,
  configure, validate, version and publish **Agents** (configuration objects owned by one
  Project). **No runtime, no conversations, no deployment, no LLM calls** — authoring only.
  Delivered **reuse-first** with an **additive Foundation change** (no new ADR, no redesign;
  existing contracts + all 1653 prior tests preserved; frozen Epics 5/6/7 untouched). Ships:
  agent **CRUD** (create/update/archive/restore/duplicate/**soft-delete**), **publish/unpublish/
  rollback** with **immutable versions**, full configuration (identity + **prompt builder** with
  live counter/preview + **provider-independent** model & voice + **typed variables incl.
  secrets**), **validation** (required/prompt-size/`{{variable}}`-refs/model+voice compatibility/
  duplicate-name/isolation), **version history + field-level comparison**, and **per-agent
  durable activity** over the M8.9 audit stream. Reuse decisions: **no new RBAC scope** (writes
  via workspace-scoped `PROJECT_MANAGE`, reads via `PROJECT_READ`); **versioning + activity reuse
  the durable Audit Platform** (one history system, not a second); CQRS/UoW/persistence seams
  unchanged. **Secrets** are retained in the domain for persistence but **redacted at every read
  boundary** (view + version snapshot). **Rollback never rewrites history** — it publishes a new
  version restored from the target. Foundation (additive): `agent_domain.py` + `agent_builder.py`
  + repos/DTOs/events/prefixes/diagnostics + one `resource_activity` projection method
  (`services.py` untouched). Application: `agent_builder.py` commands/queries/use cases into the
  existing buses. Persistence: `cp_agent` + append-only `cp_agent_version` tables + SQL repos +
  **migration `0006`**. API: thin `…/projects/{id}/agents` router. Studio: Agent list/editor/
  prompt-builder/variable-manager/version-history/rollback. 13 tests (Foundation unit + API/
  persistence e2e incl. full Definition-of-Done, versioning/rollback, audit reuse, restart
  durability, authz failure, cross-project/cross-org isolation, Studio served). **1666/1666
  repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale **DL-0098**; spec
  `docs/architecture/epic8/agent-builder.md`.
- **Durable Audit & Activity Platform (M8.9)** — hardening: replaced the temporary in-memory
  activity stream with a durable, **append-only** Audit Store + a projection read-model. A
  **storage-only** change — business logic, ownership, and every existing API contract are
  unchanged (all 1641 prior tests still green; frozen Epics 5/6/7 untouched). Foundation
  (additive read seam): new `audit.py` (`AuditRecord`/`AuditQuery`/`AuditPage`/`AuditReader`)
  + `activity.py` (`ActivityProjectionService` — organization/workspace/project/recent/user
  projections with pagination + filtering + time-range, read-model only); `RecordingEventEmitter`
  additively implements the reader (default in-memory Foundation unchanged). Persistence
  (additive): new append-only `cp_audit_event` table (`seq` PK for ordering/pagination;
  indexed filter columns) + `SqlAuditStore` (write `emit` + read `query`/`stream`, **no
  update/delete**) committed atomically by the existing `SqlUnitOfWork` + migration `0005`;
  wired as the emitter **and** audit reader in `build_foundation` (replaces the in-memory
  emitter for the persistent app, no interface change). Application (additive extensions): the
  three existing activity queries gain an optional `ActivityFilter` and read the durable
  projection (preserving `{summary, feed}` + pagination meta); new `GetRecentActivity`/
  `GetUserActivity`. API (compatible-additive): the three `/activity` routes gain optional
  `limit`/`offset`/`actor`/`event_type`/`since`/`until` params + new `…/activity/recent` and
  `…/activity/users/{user_id}`. Studio: one reusable durable activity feed with event-type/
  user/time-range filters + infinite scroll. **Audit is append-only + immutable** (never
  updated/deleted). Component/architecture spec:
  [`docs/architecture/epic8/audit-activity-platform.md`](docs/architecture/epic8/audit-activity-platform.md).
  12 tests incl. history-survives-restart (file DB), large-history pagination (81 events),
  projection rebuild from the durable log, append-only guard, API params + recent/user, Studio
  controls. **1653/1653 repository-wide; ruff clean; frozen Epics 5/6/7 untouched.** Rationale
  **DL-0097**.

## Unreleased — Epic 7 (Product Platform) · 🔒 FROZEN (M7.0F)

**Product Runtime v1.0** — Epic 7 is **architecturally frozen** as of M7.0F
(2026-07-27); the public architecture, ownership, `product → core` dependency
direction, and extension points are locked (freeze manifest:
[`docs/releases/epic7-product-runtime-v1.0.md`](docs/releases/epic7-product-runtime-v1.0.md)).
Recommended platform baseline at release: **v2.0.0**. This section rolls into a
tagged version section at release time (no tag/release performed in M7.0F).

### Added

- **ProductRuntime composition façade (M7.0E / certification finding C1)** — a minimal,
  immutable `product/runtime.py` façade that *composes* the Product Runtime artifacts a
  consumer holds: it accepts a `ProductPackage` and an optional `DeploymentPlan` and
  exposes the `CompiledProduct`, metadata, and diagnostics as read-only projections,
  guarding the package↔plan composition boundary. It **never executes, deploys, owns a
  runtime lifecycle/providers/workflows, or touches infrastructure** — realizing the
  M7.0A "Runtime Composition" façade without adding any execution surface. 7 tests
  (immutability, deterministic construction, composition, mismatch guard, no-execution
  surface, boundary). `product → core` only, unchanged.

### Certification & freeze preparation (M7.0D / M7.0E)

- **M7.0D** — independent architecture certification returned **CERTIFIED WITH
  CONDITIONS** (no architecture violations; three closure conditions).
- **M7.0E** — closed all three conditions: **C1** façade implemented (above); **C2**
  `FeatureFlag`/`TenantOverride` tuple/mapping representation documented as the
  permanent architecture (no unused `FeatureFlagSet`/`TenantOverrideSet` wrappers —
  would be dead code); **C3** the `RegistrySnapshot → BindingSnapshot` rename
  (ADR-0020) reconciled in the ADR Register and INDEX **without modifying the
  immutable ADR-0019**. Added `docs/releases/epic7-freeze-preparation.md`. Repository
  verified freeze-ready: Epic-7-only working tree, no temp/debug/TODO/placeholder,
  74/74 frozen dataclasses, zero side-effects, `product → core` only, 1556/1556 tests,
  ruff clean. No commit/push/tag/release performed.

- **Product Deployment Planning Layer (M7.0C)** — a new `platform/product/deployment/`
  subpackage turning an immutable `ProductPackage` into a validated, immutable
  **`DeploymentPlan`** (an immutable `DeploymentDescriptor` — deployment *intent* only —
  plus a machine-readable `DeploymentReport`). The layer owns deployment intent and
  nothing more: it **never performs a deployment, never calls infrastructure, and never
  starts a runtime session** (ADR-0018 applied — no new ADR introduced). Extends the
  identity chain by one link (…→Package ID→Deployment ID; content-derived, reproducible,
  timestamp-free). `DeploymentPlanner` builds and validates; `DeploymentValidator` checks
  package integrity, target compatibility, profile/target, environment + tenant overrides
  (rejecting execution-ownership keys), resource limits, feature-flag consistency,
  rollout-policy consistency, and rollback admissibility (same-major rule; verified against
  a read-only `ProductRegistry` when supplied). `DeploymentProfile` (Development…Hybrid),
  `DeploymentTarget` (Single/Multi-Tenant), `DeploymentPolicy`, `RollbackPolicy`,
  `ReleaseTarget`; `DeploymentDiagnostics` (categorized view over the shared finding set);
  byte-identical `DeploymentSerializer`. `EnvironmentProfile` and `DeploymentStrategy` are
  reused from `product.domain` (the latter extended additively with `IMMEDIATE` and
  `MANUAL_APPROVAL`). Extension seams only for scheduling/approval/orchestration/GitOps/
  Kubernetes/Terraform (none actuate). Deployment execution, infrastructure adapters,
  release pipeline, and enterprise features are explicit non-goals. `product → core` only
  (freeze preserved, AST-test-enforced). 27 tests; 1549/1549 repository-wide; ruff clean.

- **Product Package, Registry & Versioning (M7.0B-3)** — a new `platform/product/registry/`
  subpackage turning an immutable `CompiledProduct` into an immutable, content-addressed,
  distributable **`ProductPackage`** (the only deployable artifact of Product Runtime) via
  `ProductPackager`, plus an immutable read-only **`ProductRegistry`** over published
  packages. Deterministic identity chain (Manifest→Definition→Compilation→Package;
  content-derived, reproducible, timestamp-excluded); byte-identical `PackageSerializer`;
  `PackageValidator` + `IntegrityValidator` (recompute-and-compare the four content hashes,
  tamper-evident, verification only); `SignatureMetadata` + `SignatureProvider` seam (no
  cryptography — unsigned by default); `VersionManager` (transition classification +
  same-major rollback rule) + `CompatibilityMatrix` (static evaluation). Registry queries:
  latest / exact / compatible / rollback / history / certification / provenance + a unified
  `RegistryQuery`/`execute` surface; content-addressed `RegistrySnapshot`; `PackageStore`
  abstraction (in-memory default). Extension seams only for distributed/OCI/cache/mirror/
  signing (no implementation). Deployment, runtime execution, release pipeline, marketplace,
  and enterprise features are explicit non-goals. `product → core` only (freeze preserved,
  AST-test-enforced). 34 tests; 1522/1522 repository-wide; ruff clean.
- **ADR-0020** — resolves a naming collision: "Registry" nouns are reserved for the Product
  Registry of packages; the M7.0B-2 compiler binding-resolution catalog was renamed
  `RegistrySnapshot`→`BindingSnapshot` / `RegistryEntry`→`BindingSnapshotEntry` (behaviour
  unchanged; all callers + tests updated in the same change-set).

- **Product Compiler & Binding Resolution (M7.0B-2)** — a new `platform/product/compiler/`
  subpackage: a deterministic, side-effect-free 8-stage pipeline (validate → resolve
  bindings → resolve configuration → analyze dependencies → validate ownership →
  validate compatibility → produce `CompiledProduct` → produce `CompilationReport`)
  that turns a validated `ProductDefinition` into an immutable, content-addressed
  `CompiledProduct` plus a machine-readable `CompilationReport`. `BindingResolver`
  resolves manifest reference placeholders into immutable `Binding`s against an
  injected immutable `BindingSnapshot`
  ([ADR-0019](docs/adr/ADR-0019-binding-resolution-against-registry-snapshot.md)) —
  never a live Epic 6 registry, never executing/initializing/loading/communicating.
  `DependencyAnalyzer` (missing refs, cycles, invalid ordering, unreachable nodes,
  deterministic topological order); `ConfigurationResolver` (Default→Environment→
  Tenant→Deployment precedence with deep-merge, override tracing, and conflict
  reporting; runtime/request remain contracts); `CompatibilityValidator` (static
  version/schema/platform checks); `OwnershipValidator` (rejects any config asserting
  execution ownership). Byte-identical determinism for identical
  definition+snapshot+compiler version. Extension seams only for
  caching/incremental/parallel compilation (no optimization engine). Packaging,
  product registry, deployment, and runtime execution are explicit non-goals.
  `product → core` only (freeze preserved, AST-test-enforced). 34 tests;
  1488/1488 repository-wide; ruff clean.

- **Product Definition & Manifest Core (M7.0B-1)** — the first Epic 7 bundle, a new
  `platform/product/` package: the immutable authoring foundation of the Product
  Runtime, a declarative composition layer above the frozen Execution Platform
  ([ADR-0018](docs/adr/ADR-0018-product-runtime-declarative-composition-layer.md)).
  `ProductManifest` (declarative contract, data-not-code), `ProductDefinition`
  (validated + canonicalized + content-addressed, no runtime state), `ProductFactory`
  (load→parse→validate→canonicalize→construct, fail-fast), `ProductValidator`
  (schema/interface-version, required fields, SemVer, duplicate ids, reference
  well-formedness, secret integrity — no resolution), and `ProductDiagnostics`
  (deterministic finding set). Authoring-order-independent canonical hashing.
  References and secret references are inert placeholders — never resolved.
  Compilation, bindings, packaging, deployment, and registry are explicit non-goals.
  `product → core` only; no Epic 5/6 package imports it (freeze preserved,
  AST-test-enforced). 43 tests; 1454/1454 repository-wide; ruff clean.

## v1.0.0 — Execution Platform v1.0

**Status:** Production Certified · Epic 6 Frozen
**Date:** 2026-07-26
**Depends on:** Runtime Platform v1.0 (Epic 5)

The first official production baseline: the frozen **Execution Platform** — the
capabilities that turn the Epic 5 reasoning stack into an autonomous, tool-using,
multi-step agent platform.

### Major capabilities

- **Provider Runtime** — the sole execution boundary to external AI providers
  (registration, discovery, routing, failover, health, policy, tenancy).
- **Tool Runtime** — the execution layer for external capabilities an agent invokes,
  with governance, policy, observability, replay, and streaming.
- **Skill Runtime** — reusable, versioned, declarative capabilities (manifest, not code).
- **Hook Framework** — a deterministic cross-cutting lifecycle-interception library.
- **Workflow Runtime** — deterministic orchestration graphs over Skill/Tool/Hook.
- **Agent Runtime** — long-lived autonomous orchestration: goal decomposition and delegation.
- **Loop Engine** — a deterministic propose→critique→repair→validate→stop-check library.
- **Platform Integration** — the composition root wiring the certified capabilities end-to-end.
- **MCP Runtime** — the single owner of client-side Model Context Protocol communication.

### Architectural achievements

- One concern per capability; strict downward-only dependencies onto the contract floor.
- Acyclic dependency graph; capabilities are mutually independent and separately testable.
- Composition roots are the only integration points; nothing imports them back.
- Deterministic execution with offline-default adapters; capability-local, hash-chained replay.
- Capability-local observability with cross-capability correlation by `trace_id`.
- Tenant isolation across connections, credentials, and replay.

### Status

- Production Certified.
- **Epic 6 Frozen** — future work extends this baseline; it does not modify it.

See [`docs/releases/v1.0.0-release-notes.md`](docs/releases/v1.0.0-release-notes.md)
and the freeze manifest
[`docs/releases/epic6-execution-platform-v1.0.md`](docs/releases/epic6-execution-platform-v1.0.md).

## epic5-runtime-platform-v1.0 — Runtime Platform v1.0

**Status:** Production Certified · Epic 5 Frozen · **Date:** 2026-07-24

Memory, Knowledge, Context, Prompt, Reasoning, and Provider runtimes composed into
one deterministic Runtime Pipeline. See
[`docs/releases/epic5-runtime-platform-v1.0.md`](docs/releases/epic5-runtime-platform-v1.0.md).
