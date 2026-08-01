# VoxVoice — Epic 8 (Customer Platform) Implementation Plan (M8.1)

| | |
|---|---|
| **Document** | `docs/roadmap/epic8-implementation-plan.md` |
| **Status** | 🟡 **Planning — final planning milestone before implementation (M8.1)**. No code, no APIs, no schema, no implementation. |
| **Date** | 2026-07-27 |
| **Scope** | Converts the [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md) into an executable engineering roadmap: phase plan, engineering streams, domain model (bounded contexts), aggregate & API-domain identification (no tables/endpoints), UI milestones, dependency graph, per-milestone breakdown, MVP definition, release strategy, and risk register. |
| **Authority** | Subordinate to the Engineering Constitution, all ratified ADRs (ADR-0001…0022), and the frozen Epic 5/6/7 platforms. Realizes — does not redesign — the architecture fixed by the Blueprint, ADR-0021 (Studio owns no state), and ADR-0022 (Deployment Actuator owns execution). Introduces **no new ADR** (no unavoidable architectural conflict discovered). |
| **Related** | [Product Blueprint](../architecture/epic8/product-blueprint.md) · [ADR-0021](../adr/ADR-0021-studio-consumer-and-management-surface.md) · [ADR-0022](../adr/ADR-0022-deployment-actuator-runtime-actuation.md) · [Epic 7 Freeze](../releases/epic7-product-runtime-v1.0.md) · [migration-progress](migration-progress.md) · [INDEX](../INDEX.md) |

> **Planning-only notice.** Every milestone, aggregate, API domain, and screen below is a
> *plan of work*, not existing behaviour. Estimates are relative complexity, not calendar
> commitments. Implementation begins only after this plan is accepted.

---

## Standing constraints (bind every milestone)

1. **Epics 5/6/7 are frozen** — no milestone modifies them. Epic 8 reaches the platform
   only through the **Control Plane** (`platform/api/`), the **Product Runtime** (Epic 7
   artifacts), the **event log / gateway**, and the **Deployment Actuator** (ADR-0022).
2. **Studio owns no runtime state (ADR-0021).** Every Studio write is an authoring
   artifact or a request; every read is a projection. The Allowed/Forbidden Writes lists
   are milestone acceptance criteria.
3. **The Actuator is the only execution owner (ADR-0022).** No other Epic 8 component
   actuates infrastructure; Product Runtime stays declarative.
4. **One writer per fact (§A2.5); accepted config must act (§A2.6).** Studio forms bind to
   capability flags (§A6), never vendors.
5. **New top-level directories require a structural placement ADR** (CODING_STANDARDS §2 —
   the ADR-0018/0022 precedent). This plan *identifies* the two needed
   (`studio/` frontend; a Control-Plane customer-management surface if it warrants its own
   package) as **kickoff entry-criteria for M8.2**, and does not mint them here — they are
   structural placement records, not architectural conflicts.

---

# 1. Executive Summary

Epics 5–7 built and froze the product (runtime, execution platform, declarative Product
Runtime). ADR-0021 fixed that the customer platform (**VoxVoice Studio**) is a Consumer +
management surface that owns no state; ADR-0022 fixed that a **Deployment Actuator** owns
execution. This plan turns that settled architecture into work multiple engineers can
start immediately.

It slices Epic 8 into **9 implementation phases** (M8.2–M8.10) along a deliberate
**activation spine** — signup → org → workspace → agent → prompt → test → deploy → monitor
— so the smallest useful product ships first and everything else is additive. Work is
split into **7 mostly-independent engineering streams** (Control-Plane Backend, Studio
Frontend, Actuator/DevOps, AI/Coach, SDK, Docs, QA) coordinated by stable contracts
(OpenAPI + event schemas) so streams progress in parallel with minimal merge conflicts.
It identifies **8 bounded contexts** and their **aggregates** (not tables), **API
domains** (not endpoints), and a **UI milestone map** (Must/Should/Future), then gives a
**dependency graph** showing what runs in parallel and what blocks, a **per-milestone
breakdown** (purpose/inputs/outputs/deps/complexity/risk/test/DoD), the **MVP**, a
four-gate **release strategy** (Alpha→Private Beta→Public Beta→GA), and a **risk
register** across technical/product/operational/scaling/security.

The plan introduces **no new architecture and no new ADR** — it executes what is already
ratified. Implementation begins at M8.2 (Foundation) upon acceptance.

---

# 2. Epic 8 Phase Plan

Nine independently-deliverable phases. Each ends at a demoable increment.

| Phase | Milestone | Name | Ships (customer-visible) | Depends on |
|---|---|---|---|---|
| **P1** | M8.2 | **Customer Platform Foundation** | Signup/login, org auto-create, tenancy, RBAC skeleton, Studio shell + design system, ⌘K nav | Control Plane (frozen), design system |
| **P2** | M8.3 | **Organizations & Workspaces** | Create/manage orgs, members, workspaces, environments, secrets, settings | P1 |
| **P3** | M8.4 | **Projects** | Project grouping, project board, settings | P2 |
| **P4** | M8.5 | **Agent Builder (+ Prompts)** | Author Agent config (persona/language/providers/policy) + Prompt library/versioning | P3, Prompt/Agent/Provider runtimes (frozen) |
| **P5** | M8.6 | **Knowledge** | Upload/import docs, chunk inspector, attach to agents, retrieval test | P4, Knowledge Runtime (frozen) |
| **P6** | M8.7 | **Testing Playground** | In-browser talk-to-agent, **live event stream + per-stage latency**, replay | P4, Event Gateway + Replay (frozen) |
| **P7** | M8.8 | **Deployment (Actuator)** | Product manifest → compile → **deploy** (Local + first target), rollback, version history | P4, Product Runtime (frozen), **ADR-0022 Actuator** |
| **P8** | M8.9 | **Monitoring & Analytics** | Sessions list/detail, latency/cost/usage/quality dashboards, logs, alerts | P6/P7, event-log projections (frozen) |
| **P9** | M8.10 | **Marketplace Foundation** *(Future)* | Browse/fork templates, publish flow (curated) | P7, Product Registry (frozen) |

**Post-MVP, demand-gated (fold into later minor milestones, not on the spine):** Tools
(M8.5.x), MCP (M8.x), Workflows graph editor, Memory config, Coach/AI-PM (ADR-0024),
Billing (ADR-0023), SSO/SCIM, white-label. Each is additive over an existing frozen owner.

---

# 3. Engineering Streams

Seven streams, each able to progress against stable contracts (OpenAPI + event schemas +
the design system) rather than each other's code.

| Stream | Owns | Primary surface | Parallelism key |
|---|---|---|---|
| **S1 — Control-Plane Backend** | Customer-management APIs, tenancy/RBAC, authoring-artifact persistence, request routing to runtimes/Actuator | `platform/api/` extensions | Publishes OpenAPI first; frontend mocks against it |
| **S2 — Studio Frontend** | Studio web app, design system, screens, projections, ⌘K | `studio/` (new top-level; ADR at M8.2) | Builds on OpenAPI mocks + event-schema fixtures |
| **S3 — Actuator / DevOps** | Deployment Actuator, Infrastructure Adapters, Local + first target | `platform/actuator/` (ADR-0022) | Isolated package; Local adapter unblocks everything offline |
| **S4 — AI / Coach** | Coach (AI-PM), eval/LLM-judge integration | composes frozen runtimes | Starts after P4/P8 data exists; no frozen change |
| **S5 — SDK** | Client SDK parity for test/embed (TS core + React) | `sdk/` (exists) | Tracks event-channel + control API; additive bindings |
| **S6 — Documentation** | Component specs (§A11.3), API/event catalogues, INDEX upkeep | `docs/` | Continuous; gates each DoD |
| **S7 — QA / Testing** | Contract suites, e2e-with-fakes, latency-regression gates, accessibility | `platform/tests/` + Studio tests | Consumes the same contracts; runs per push |

**Coordination contract (the anti-merge-conflict mechanism):** S1 publishes/OpenAPI +
event schemas per milestone *before* implementation; S2/S5/S7 build against generated
mocks/fixtures; S3 develops behind the `InfrastructureAdapter` contract with a Local
default. Cross-stream changes land as additive contract versions (§A11.4), never breaking
edits.

---

# 4. Domain Model (bounded contexts)

Eight bounded contexts. **Ownership is already fixed by ADR-0021's Ownership Matrix** — this
plan restates it as engineering-stream ownership. Studio is the surface for all; the
"Owner" column is where the *fact* lives.

| Bounded context | Owner (authoritative) | Epic-8 stream that surfaces it | Notes |
|---|---|---|---|
| **Identity & Tenancy** | Control Plane / System-of-Record (`org_id`) | S1 | orgs, workspaces, members, RBAC, envs, secrets |
| **Authoring** | Studio writes artifacts; runtimes consume | S1 + S2 | Agent config, Prompts, Product manifests |
| **Agents** | Agent Runtime (Epic 6, frozen) | S1/S2 | Studio authors config; runtime owns behaviour |
| **Knowledge** | Knowledge Runtime (Epic 5, frozen) | S1/S2 | import requests; projections |
| **Products** | Product Runtime (Epic 7, frozen) | S1/S2 | manifest → compile → package |
| **Deployments** | **Deployment Actuator** (Epic 8, ADR-0022) | S3 | execution state; reads frozen plan |
| **Sessions & Analytics** | Event log / `metric.*` (frozen producers) | S1 (projections) + S2 | Studio never writes events |
| **Billing / Marketplace** *(future)* | Billing Runtime (future, ADR-0023) / Product Registry | S1/S4 | not on MVP spine |

No context boundary crosses a frozen Epic; each is a surface or a new peer package
(`actuator/`, `studio/`).

---

# 5. Database Planning (aggregates & relationships — no tables)

**Aggregates identified only** (schema is out of scope for this milestone). Frozen Epics
already own their durable state; Epic 8 adds **only** the authoring/management/execution
aggregates below, each with a single writer (§A2.5).

| Aggregate | Owner | Key relationships |
|---|---|---|
| **Organization** | Control Plane | has many Workspaces, Members, ApiKeys; root of billing/identity |
| **Workspace** | Control Plane | under Organization; has Environments, Secrets, Members(roles), Projects |
| **Membership / Role Binding** | Control Plane | links Member ↔ Org/Workspace ↔ Role (RBAC, ADR-0021 §5) |
| **Environment** | Control Plane | under Workspace; a deployment target profile (Epic 7 `EnvironmentProfile`) |
| **Secret Reference** | Control Plane (via CredentialProvider seam) | referenced by Tools/MCP/Adapters; never stores plaintext |
| **Project** | Control Plane | under Workspace; groups Agents/Products |
| **Agent Definition (authoring record)** | Studio→Control Plane authoring store | references Prompts, Knowledge, Tools; compiles into a Product |
| **Prompt (authoring record)** | Prompt Runtime authoring surface | versioned; attached to Agents |
| **Product Authoring Record** | Product Runtime (Epic 7) | wraps Agent + deps; produces immutable Package |
| **Deployment Execution Record** | **Deployment Actuator** (ADR-0022) | references immutable `DeploymentPlan`; holds lifecycle/rollout/approval state |
| **Approval** | Deployment Actuator / Control Plane | gates a Deployment Execution Record |
| **User Preference / Studio State** | Studio (Consumer) | per-user; owns nothing platform-authoritative |

**Sessions, transcripts, metrics, audit events, memory, knowledge chunks, provider state**
are **not** new aggregates — they are owned by frozen Epics and consumed as projections.

---

# 6. API Planning (domains — no endpoints)

**API domains only.** All are Control-Plane surfaces (stateless, §A4); none bypasses a
frozen owner. Endpoint/verb design is a per-milestone task, not this milestone's.

| API domain | Purpose | Backs modules |
|---|---|---|
| **Auth & Identity** | signup/login/SSO/MFA, session tokens, scoped client creds | Onboarding, Security |
| **Organizations & Members** | org/workspace/member/RBAC management | Orgs, Workspaces |
| **Environments & Secrets** | env CRUD, secret references | Workspaces, Deployments |
| **Authoring** | Agent config, Prompt, Product manifest CRUD (declarative artifacts) | Agents, Prompts, Products |
| **Knowledge** | import requests, retrieval-test proxy | Knowledge |
| **Testing** | start test session, stream events, run evals | Testing Playground |
| **Deployment** | submit deploy/rollback/promote *requests*, read status (→ Actuator) | Deployments |
| **Projections (read)** | sessions, analytics, logs, audit, deployment history — read-only | Sessions, Analytics, Logs, Monitoring, Audit |
| **Billing** *(future)* | plans, usage, invoices, quotas | Billing |
| **Marketplace** *(future)* | browse/publish curated packages | Marketplace |

Every mutating domain enforces org-scoping + RBAC + write-time capability validation
(§A2.6/§A6); every read domain returns projections Studio does not own.

---

# 7. UI Planning (screen milestones)

From the Blueprint's screen inventory (§9), grouped by priority.

| Priority | Screens | Phase |
|---|---|---|
| **Must Have (MVP spine)** | Signup, Create Org, Create Workspace, Studio shell + nav, **Agent Builder**, Prompt editor, **Test panel (live events + latency)**, **Deploy view + status**, **Session detail**, Overview dashboard, API keys/MFA | P1, P4, P6, P7, P8 |
| **Should Have** | Members/RBAC UI, Environments, Knowledge upload + chunk inspector, Version history/diff, Rollback, Latency/Cost/Usage dashboards, Logs explorer, Approval queue, Monitoring/alerts | P2, P5, P7, P8 |
| **Future** | Workflows graph editor, MCP connect, Memory settings, Coach panel, Tools catalog, Marketplace, Branding/white-label, Custom reports, Audit explorer, Platform-staff console | P9 + demand-gated |

**Design-system-first:** S2 builds the component system + theme (dark default, §19) and
accessibility (WCAG 2.2 AA) in P1 so every later screen composes it — no per-screen
restyling, no horizontal-scroll regressions.

---

# 8. Dependency Graph

```
                         ┌──────────────── FROZEN PLATFORM (Epic 5/6/7) ────────────────┐
                         │  Control Plane · Runtimes · Product Runtime · Gateway · Replay │
                         └───────────────────────────┬───────────────────────────────────┘
                                                     │ (contracts only; never modified)
        S6 Docs / S7 QA (continuous, every milestone)│
                                                     ▼
   M8.2 Foundation ──┬──► M8.3 Org/Workspace ──► M8.4 Projects ──► M8.5 Agent Builder ─┬─► M8.6 Knowledge
   (S1+S2 shell,     │                                                                  │
    OpenAPI+design)  │                                                                  ├─► M8.7 Testing Playground (S2+S5)
                     │                                                                  │
   S3 Actuator ──────┘ (Local adapter, DEVELOPS IN PARALLEL from day 1) ───────────────┴─► M8.8 Deployment ──► M8.9 Monitoring/Analytics
                                                                                                                     │
                                                                                              M8.10 Marketplace (Future) ◄┘
```

**Parallelizable from day 1:**
- **S3 (Actuator + Local adapter)** is an isolated package — builds against the frozen
  `DeploymentPlan` and its own adapter contract with **zero dependency on Studio/Control-
  Plane progress**; it only *joins* the spine at M8.8.
- **S2 (Studio shell + design system)** proceeds against S1's OpenAPI mocks before any real
  endpoint exists.
- **S5 (SDK)** tracks the frozen event channel independently; needed by M8.7.
- **M8.6 (Knowledge)** and **M8.7 (Testing)** both hang off M8.4 and can run in parallel.

**Blockers (critical path):** M8.2 → M8.3 → M8.4 gate almost everything (identity → agents).
M8.4 is the pivotal unlock. M8.8 needs both M8.4 (a product to deploy) **and** S3's
Actuator (independently ready). **Governance blocker:** the `studio/` (and any new
Control-Plane management-package) placement ADR must be accepted at **M8.2 kickoff** before
those directories land.

---

# 9. Milestone Breakdown

Per-milestone: Purpose · Inputs · Outputs · Dependencies · Complexity (S/M/L/XL) · Risk ·
Test strategy · Definition of Done. DoD everywhere additionally requires: component
specs (§A11.3), tests-in-change-set, INDEX linkage, changelog, ruff/lint clean, org-scoped
+ RBAC + audit verified, and **no frozen-Epic change** (import-boundary test green).

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | DoD (milestone-specific) |
|---|---|---|---|---|---|---|---|---|
| **M8.2 Foundation** | Auth, tenancy, RBAC skeleton, Studio shell, design system | Blueprint, ADR-0021, Control Plane | Signup/login, org auto-create, nav+⌘K, design system, OpenAPI baseline | frozen Control Plane | **L** | Med | auth/RBAC unit + e2e-with-fakes; a11y baseline | A user signs up, lands in a workspace, navigates; placement ADRs accepted |
| **M8.3 Org/Workspace** | Manage orgs/workspaces/members/envs/secrets | M8.2 | Members+RBAC UI/API, envs, secret refs | M8.2 | **M** | Med | RBAC matrix tests; secret-redaction test | Owner invites members, creates workspace+env, roles enforced+audited |
| **M8.4 Agent Builder** | Author Agent config + Prompts | M8.3, Agent/Prompt/Provider runtimes | Agent Builder, Prompt library/versioning, capability-flag-validated forms | M8.3 | **L** | Med | authoring round-trip; write-time capability validation | Agent config saved+versioned; impossible provider config rejected at write |
| **M8.5 Knowledge** | Attach knowledge to agents | M8.4, Knowledge Runtime | Upload/import, chunk inspector, retrieval test | M8.4 | **M** | Med | ingestion happy/failure; retrieval projection | Doc imported, chunked, attached; failures surfaced honestly |
| **M8.6 Testing Playground** | Talk-to-agent with live events + latency | M8.4, Gateway, Replay, SDK | In-browser test, live event stream, per-stage latency, replay | M8.4, S5 | **L** | **High** | latency-surface accuracy; event-ordering; gateway load | User talks to agent in-browser; sees live events + voice-to-voice latency; replays it |
| **M8.7 Deployment** | Compile + deploy + rollback | M8.4, Product Runtime, **Actuator (S3)** | Manifest editor, compile report, deploy(Local+first target), rollback, history | M8.4, ADR-0022 Actuator | **XL** | **High** | Actuator FSM determinism; adapter contract suite; rollback; idempotency/cancel | Product deploys to an env via Actuator; rollback works; history immutable |
| **M8.8 Monitoring/Analytics** | See health/outcomes | M8.6/M8.7, event-log projections | Sessions list/detail, latency/cost/usage/quality dashboards, logs, alerts | M8.6/M8.7 | **L** | Med | projection-correctness vs event log; no-fake-metric check | Dashboards render truthful projections; alerts fire on real thresholds |
| **M8.9 Actuator target #2 / hardening** | Second infra target + prod-hardening | M8.7 | 2nd `InfrastructureAdapter`, canary/blue-green where declared | M8.7 | **L** | Med | 2nd-adapter contract suite; degraded-mode declaration | New target added with no Actuator core change (additive proof) |
| **M8.10 Marketplace (Future)** | Share/fork templates | M8.7, Product Registry | Browse/fork/publish (curated) | M8.7 | **M** | Low | package integrity/signature seam | Template forked into a workspace; publish gated+reviewed |

*(Tools/MCP/Workflows/Coach/Billing enter as demand-gated minor milestones after MVP,
each an additive surface over an existing frozen owner — same DoD discipline.)*

---

# 10. MVP Definition

**The smallest usable product = the activation spine.** A customer can:

1. **Sign up** (M8.2) → 2. **Create Organization** (M8.2) → 3. **Create Workspace** (M8.3)
→ 4. **Create Agent** (M8.4) → 5. **Configure Prompt** (M8.4) → 6. **Test Agent** live,
with events + latency (M8.6) → 7. **Deploy** via the Actuator (Local/first target, M8.7) →
8. **Monitor** sessions + latency (M8.8).

**MVP = M8.2 + M8.3 + M8.4 + M8.6 + M8.7 + M8.8** (the S3 Actuator Local adapter is the
minimum deployment target). **Explicitly optional for MVP:** Knowledge (M8.5), Tools, MCP,
Workflows, Coach, Billing, Marketplace, SSO, multi-target deploy. Everything off the spine
is additive and demand-gated.

**North-Star activation metric:** time-to-first-test (signup → hearing the agent) — the
whole MVP is sequenced to minimize it.

---

# 11. Release Strategy

| Gate | Audience | Entry criteria | Scope |
|---|---|---|---|
| **Alpha** | Internal + **VoxHire dogfood** | MVP spine works end-to-end with the Local adapter; frozen-Epic tests green | Feedback on the activation spine; no external SLA |
| **Private Beta** | Hand-picked design partners (P2/P5/P7) | Real first infra target (K8s or Docker) live; approvals + audit; latency dashboard truthful | Governed deploys, monitoring; NDA; close support |
| **Public Beta** | Self-serve Free/Pro | Billing/metering (ADR-0023) + quotas; second infra target; a11y AA; abuse/rate limits | Open signup; usage-metered; documented limits |
| **GA** | All tiers incl. Business/Enterprise | SSO/SCIM path, SLA, security review passed, marketplace foundation, white-label option | Production commitments; Enterprise onboarding |

Each gate is a quality bar, not a date; a feature ships to a gate only when its DoD +
that gate's entry criteria are met.

---

# 12. Risk Register

| # | Category | Risk | Mitigation |
|---|---|---|---|
| RT1 | **Technical** | Live in-browser test latency-surface is hard to make accurate & smooth (M8.6) | Reuse frozen Gateway/Replay + `metric.turn`; never fabricate a number; load-test the gateway early |
| RT2 | **Technical** | Actuator ↔ real infra is the deepest new work (M8.7, XL) | Local adapter first (offline, deterministic); one real target; adapter contract suite; ship behind Private Beta |
| RT3 | **Technical** | Studio accidentally caches execution/session state as truth (ADR-0021 breach) | Forbidden-Writes as CI acceptance; import-boundary test; projection-only reads reviewed |
| RP1 | **Product** | Scope explosion across 24 modules starves the spine (Blueprint R1) | MVP = spine only; everything else demand-gated; ruthless phase ordering |
| RP2 | **Product** | Builders don't feel the "ownership" differentiator | Make live events + latency + replay first-class in M8.6, not a buried tab |
| RO1 | **Operational** | Actuator is a new durable-state service to run (Blueprint R2) | Treat as a Stage-2+ control-plane service (like the dispatcher); Local adapter for dev; one target in prod |
| RO2 | **Operational** | Cross-stream contract drift causes merge pain | S1 publishes OpenAPI + event schemas first; additive versioning (§A11.4); mocks/fixtures |
| RS1 | **Scaling** | Projection/dashboard queries over a growing event log get slow (M8.8) | Read-model projections + retention/archival (§A7); no cross-request in-process state (§A9) |
| RS2 | **Scaling** | Many concurrent deploys per org/env | Per-(product,env) concurrency control + durable queue in the Actuator (ADR-0022) |
| RSec1 | **Security** | Infra credentials/secrets exposure via the Actuator | CredentialProvider seam; redaction at sink; least-privilege per adapter; secrets never in artifacts |
| RSec2 | **Security** | Privilege escalation in deploy/approval | Separation of duties (author≠approver); org-scoped RBAC on every action; event-log audit |
| RG1 | **Governance** | New top-level dirs (`studio/`) land without a placement ADR | Make the placement ADR an M8.2 kickoff entry-criterion (CODING_STANDARDS §2) |

---

# 13. Final Recommendation

**Accept this plan and begin implementation at M8.2 (Customer Platform Foundation).**
Concretely:

1. **Kick off three streams immediately in parallel:** S1 (Control-Plane OpenAPI + tenancy),
   S2 (Studio shell + design system against mocks), and **S3 (Actuator + Local adapter)** —
   S3 is fully isolated and de-risks the XL deployment work long before M8.7.
2. **Gate M8.2 on the two structural placement ADRs** (`studio/` and any Control-Plane
   management package) — the only governance action required before code lands; they are
   placement records, not architectural changes, so they do not reopen the Blueprint or
   ADR-0021/0022.
3. **Drive to the MVP spine** (M8.2/8.3/8.4/8.6/8.7/8.8) for **Alpha**, dogfooding through
   VoxHire; hold Knowledge/Tools/Coach/Billing/Marketplace off the critical path.
4. **Author ADR-0023 (Billing) and ADR-0024 (Coach)** before Public Beta and their
   respective features — not now (no unavoidable conflict exists at planning time).
5. **Every milestone keeps the frozen Epics untouched** (import-boundary test green) and
   honours ADR-0021's Forbidden Writes and ADR-0022's execution ownership as acceptance
   criteria.

This plan introduces no new architecture and no new ADR; it is the executable projection
of what is already ratified. On acceptance, **implementation begins at M8.2.**

---

*Planning-only milestone (M8.1). No code, no APIs, no database schema, no implementation,
no architecture redesign, no new ADR. Subordinate to the Engineering Constitution, ADR-0001…
0022, and the frozen Epic 5/6/7 platforms.*
