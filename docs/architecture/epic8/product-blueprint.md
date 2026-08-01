# VoxVoice — Epic 8.0A Product Blueprint & Customer Platform Architecture

| | |
|---|---|
| **Document** | `docs/architecture/epic8/product-blueprint.md` |
| **Status** | 🟡 **Design only (M8.0A)** — no code, no APIs, no database tables. Master product blueprint. |
| **Date** | 2026-07-27 |
| **Milestone** | Epic 8 (Customer Platform), Phase 0A |
| **Scope** | The complete product blueprint for VoxVoice as a customer-facing platform: vision, personas, roles, modules, journey, navigation, screens, hierarchy, SaaS/AI/deployment/analytics/billing/security/marketplace architecture, the AI Product Manager, UI philosophy, competitive analysis, roadmap, risks, and the final recommendation. |
| **Authority** | Subordinate to the Engineering Constitution (Part A/B), all ratified ADRs, and the frozen Runtime Platform v1.0 (Epic 5), Execution Platform v1.0 (Epic 6), and Product Runtime v1.0 (Epic 7). This document **may not contradict any of them**; where it needs a new decision it *recommends* an ADR, never ratifies one. |
| **Related** | [Engineering Constitution v2](../engineering-constitution-v2.md) · [Epic 5 freeze](../../releases/epic5-runtime-platform-v1.0.md) · [Epic 6 freeze](../../releases/epic6-execution-platform-v1.0.md) · [Epic 7 freeze](../../releases/epic7-product-runtime-v1.0.md) · [ADR register](../../adr/README.md) · [INDEX](../../INDEX.md) |

> **Non-implementation notice.** Per the Epic 8.0A mandate this milestone defines the
> product blueprint only. It creates no source, no endpoint, no schema, and touches no
> frozen Epic. Every "screen", "table", "endpoint", or "flag" named below is a *design
> intent* to be realized by a later, separately-certified Epic 8 milestone — never a
> claim of existing behaviour.

---

## The single organizing principle (read this first)

The runtime is the product (Constitution §A1). Epics 5–7 already built and froze the
thing customers pay for: a provider-agnostic voice runtime (Epic 5), an execution
platform of tools/skills/workflows/agents/MCP (Epic 6), and a declarative Product
Runtime that compiles a manifest into a deployable package (Epic 7).

**Epic 8 does not build a second product. It builds the _surface_ humans use to operate
the product they already own.** The Customer Platform — named **VoxVoice Studio** in
this blueprint — is:

1. a **management & authoring surface** over the stateless **Control Plane** (§A4) and
   the **Product Runtime** (Epic 7 manifest → definition → compilation → package →
   deployment), and
2. itself a **Consumer** in the Constitution's exact sense (§A0): it configures agents,
   starts sessions, receives live events, and issues control commands — it never touches
   audio, providers, or conversation mechanics.

This single framing is what makes the blueprint safe: **Studio owns no runtime state.**
Every noun a customer manipulates in Studio is a projection of, or an authoring input
to, a capability that an already-frozen Epic owns. Studio reads the `event_log` and its
projections; it writes `ProductManifest`s and Agent configuration; it calls the Control
Plane. It is a *client of the platform*, architecturally identical to VoxHire or any
sales/support consumer — with elevated, first-party management scope.

This is proposed for ratification as **ADR-0021** (§ "User Roles" and § "Final
Recommendation").

---

# 1. Executive Summary

VoxVoice has, across Epics 5–7, built and frozen a rare asset: a **fully-owned,
provider-agnostic voice AI runtime** with a declarative product-composition layer on
top. What it does **not** yet have is the thing every competitor leads with — a
world-class **customer platform**: the console where a developer signs up, builds a
voice agent, connects knowledge and tools, tests it, deploys it, watches it run, and
improves it.

This blueprint designs that platform end-to-end as **VoxVoice Studio**. Its thesis is
contrarian and deliberate: where Retell, Vapi, Bland, and ElevenLabs sell *access to a
black-box engine*, VoxVoice sells *ownership of an observable, controllable, portable
runtime* — and Studio is the surface that makes that ownership tangible. Every
differentiator the runtime already earned (live event streams, mid-call control,
per-stage latency truth, provider portability, deterministic replay) becomes a
first-class product feature in Studio, not a buried API detail.

The blueprint delivers: 3 foundational statements (Vision/Mission/Philosophy), 11
customer personas, an 11-role RBAC model, 24 product modules each traced to an owning
capability, the complete signup→production customer journey, a full navigation and
screen inventory (V1/V2/Future), a future-proof product hierarchy, and the SaaS, AI,
deployment, analytics, billing, security, and marketplace architectures — plus an
internal **AI Product Manager** ("Coach") that helps customers build better agents. It
closes with a competitive analysis, a V1→V3 roadmap, a risk register, and a final
recommendation to ratify ADR-0021 and open Epic 8 implementation at M8.1.

**Nothing here modifies a frozen Epic. Everything here is buildable on existing seams.**

---

# 2. Product Vision

**Vision.** Every organization can build, deploy, and continuously improve a
production-grade voice agent — in an afternoon, not a quarter — without surrendering
ownership of the conversation, the data, or the vendor relationships underneath it.

**Mission.** Give builders the fastest path from *an idea for a voice agent* to *a
low-latency, interruptible, observable, improving agent in production* — on a runtime
they fully own and can point at any provider they choose.

**The problem VoxVoice solves.** Voice AI today forces a false choice. Either you rent a
black-box engine (Vapi/Retell/Bland) — fast to start, but you get no live events, no
mid-call control, no latency truth, and you're locked to their provider stack and their
margins — or you assemble your own from raw STT/LLM/TTS SDKs and spend six months
rebuilding turn-taking, interruption, memory, and observability before you ship
anything. VoxVoice already built the hard middle (Epics 5–7). Studio turns that owned
runtime into a product anyone can drive.

**Why customers choose VoxVoice.**
- **You own the loop, not just the call.** Live typed events, mid-call control, and
  deterministic replay are product features, not enterprise add-ons (Constitution I2).
- **Latency is a visible feature, not a marketing number.** Per-stage p50/p95 on every
  turn, in the dashboard, on every agent (I1).
- **Providers are configuration, not lock-in.** Swap Deepgram↔any STT, Groq↔Anthropic,
  Cartesia↔any TTS with a config change and a passing contract test — no rewrite (I3).
- **Portable by construction.** An agent is a declarative `ProductManifest` (Epic 7),
  not rows in a vendor's private database. Export, diff, version, and redeploy it.

**Why VoxVoice is different.** Competitors optimize for *time-to-first-call*. VoxVoice
optimizes for *time-to-first-call **and** the next five years of owning, observing, and
improving that call*. The runtime moat (owned turn intelligence, provider neutrality,
observability, replay) is real and frozen; Studio is the surface that finally makes it
sellable.

---

# 3. Product Philosophy

1. **The platform is the product; Studio is its face.** Studio never grows a private
   brain. If a behaviour isn't owned by a runtime capability, Studio doesn't invent it —
   it recommends an ADR (Constitution authority hierarchy).
2. **Configuration is the interface.** Everything a customer builds is a declarative
   artifact (an Agent config, a `ProductManifest`, a `DeploymentPlan`) — authored in a
   form, editable as code, versioned, diffable, and portable. No hidden state.
3. **Accepted config must act (§A2.6).** If a Studio form accepts a field, the runtime
   honours it or the form rejects it at write time. Studio surfaces capability flags
   (§A6) so impossible configurations are impossible to save.
4. **Observability is a feature, not a tab.** Latency, cost, events, and replay are
   present at every altitude — agent, session, turn — because the runtime already emits
   them (I1/I2).
5. **Progressive disclosure, no dead ends.** A beginner ships with defaults; an expert
   drops to the manifest, the raw event stream, and the API. Same object, more depth.
6. **Multi-tenant and enterprise-ready from line one.** Org-scoping, RBAC, audit, and
   isolation are the substrate (§A11.2), not a later "Enterprise" bolt-on.
7. **Deterministic where it matters.** Deploys, rollbacks, replays, and tests are
   reproducible; only the conversation *content* is probabilistic (§A2.4).

---

# 4. Customer Personas

Each persona lists **problems → goals → expected workflow**. "Consumer" below is the
Constitution term (§A0): the application built on VoxVoice.

| # | Persona | Problems | Goals | Expected workflow |
|---|---|---|---|---|
| P1 | **Individual / Indie Developer** | Wants a voice agent this weekend; no time to build turn-taking or observability | Ship a working agent fast, cheaply, and understand *why* it behaves as it does | Signup → template → tweak prompt/voice → test in browser → deploy → grab embed/SDK snippet |
| P2 | **Startup (seed/Series A)** | Voice is core to the product; can't afford black-box lock-in or a 2-quarter build | Fast iteration + provider portability + a path that scales without a rewrite | Workspace per env → agents in Git-like versions → CI deploy → live dashboards |
| P3 | **SMB (non-technical operator)** | Needs a receptionist/booking/support line; no engineers | A guided builder, sensible defaults, phone number, "it just works" | Guided wizard → connect calendar tool → buy number → publish → weekly report |
| P4 | **Enterprise platform team** | Many agents, many teams, compliance, SSO, cost control, audit | Governed multi-workspace platform, RBAC, SSO, quotas, audit, private providers | Org → workspaces per BU → RBAC + SSO → approval-gated deploys → cost/audit dashboards |
| P5 | **HR / Talent team** (e.g. VoxHire) | Screening/interview at scale, fair and consistent, reviewable | Reliable interview agents, transcripts, structured scoring, replay for QA | Interview agent from template → knowledge (rubric) → schedule sessions → review replays |
| P6 | **Recruitment agency** | Runs voice screening for many client orgs | Per-client isolation, white-label, per-client reporting | Org → workspace per client → white-label domain → per-client analytics |
| P7 | **Customer Support / CX** | High call volume, escalation, tool actions (refunds, lookups) | Deflect & resolve calls, safe tool use with approvals, QA on transcripts | Support agent → tools (CRM/refunds w/ approval gates) → deploy → monitor + QA replay |
| P8 | **Healthcare** | Intake/reminders/triage; PII/consent/retention are hard constraints | Compliant agents, PII redaction, retention control, audit | Agent → PII-redaction policy + retention profile → BAA/compliance settings → deploy |
| P9 | **Education** | Tutoring, language practice, assessment at scale | Patient, multilingual, low-latency agents; progress analytics | Tutor agent → multilingual voices → knowledge (curriculum) → cohort analytics |
| P10 | **Banking / Financial services** | Auth, fraud, strict audit, human-in-the-loop for money movement | Secure agents, approval-gated tools, complete audit trail, SSO | Agent → approval-gated tools (§A6/Tool policy `PENDING`) → audit + SSO → deploy |
| P11 | **Government / Public sector** | Accessibility, data residency, procurement, transparency | Accessible, region-pinned, auditable agents | Agent → region placement (Stage 4) → accessibility profile → audit export |

**Cross-persona truth:** P1–P3 want *speed and defaults*; P4/P10/P11 want *governance and
isolation*; P5–P9 want *domain templates and reviewable outcomes*. Studio serves all
three by making the same object (an Agent → a Product) progressively deeper, never
forked into separate products.

---

# 5. User Roles (RBAC)

RBAC is org-scoped on every query — the audit-proven discipline retained from Phase 1
(§A11.2). Roles are **additive scopes**, assignable per-workspace (except the two
platform/org-global roles). This is the customer-facing projection of the tenancy model
already implemented across Epics 5–7 (`org_id` isolation everywhere).

| Role | Scope | Core permissions | Cannot |
|---|---|---|---|
| **Platform Super Admin** | Platform (first-party VoxVoice staff) | Manage plans, feature flags, marketplace review, platform health, impersonate-with-audit | Read tenant conversation content without an audited, consented support-grant |
| **Organization Owner** | Org | Everything in the org: billing, members, workspaces, SSO, delete org | Cross into another org |
| **Billing Admin** | Org | Plans, payment methods, invoices, quotas, cost dashboards | Edit agents / deploy |
| **Workspace Admin** | Workspace | Members, environments, secrets, deploy policy, workspace settings | Change org billing/SSO |
| **Developer** | Workspace | CRUD agents/products/tools/knowledge, deploy to non-prod, run tests, read logs | Approve prod deploys (unless also Approver), edit billing |
| **Agent Designer** | Workspace | Author agents/prompts/voices/knowledge; run tests | Deploy to prod, edit tools' code/secrets |
| **QA / Reviewer** | Workspace | Read sessions/replays, run test suites, comment, flag regressions | Edit agents, deploy |
| **HR / Recruiter** (domain) | Workspace | Run/schedule sessions, read transcripts/scores/replays for their agents | Edit agent config, deploy, see billing |
| **Analyst** | Workspace/Org | Read all analytics, build/export reports, wagon-wheel/cost/quality views | Edit agents, deploy, read secrets |
| **Approver** | Workspace | Approve/deny approval-gated deploys and `PENDING` tool actions (§A6 Tool policy) | Author or self-approve own deploy (separation of duties) |
| **Read-Only** | Org/Workspace | View everything permitted; export nothing sensitive | Any mutation |

**Design rules.** Least privilege by default; separation of duties on deploy/approval;
every mutation and every privileged read is written to the audit log (which *is* the
event log — §A11.2); impersonation is possible only for Super Admin, always audited,
always consented. SSO group→role mapping (Stage 4) is additive.

---

# 6. Product Modules (complete breakdown)

24 modules. **Every module names the frozen capability that owns its data** — this is
the column that proves the blueprint doesn't reinvent the platform. "Owner (runtime)" =
where the truth lives; "Studio owns" = only the surface.

| # | Module | Purpose | Owner (runtime capability) | Primary screens | Key relationships | Future extensions |
|---|---|---|---|---|---|---|
| 1 | **Platform** | First-party operation of VoxVoice itself | Control Plane + platform config | Platform admin, health, flag console | above all orgs | multi-region ops (Stage 4) |
| 2 | **Organizations** | Top tenant boundary; billing & identity root | `org_id` tenancy (all Epics) | Org home, members, SSO, billing | contains Workspaces | org groups / holding-co (Future) |
| 3 | **Workspaces** | Team/BU boundary; env & secret scope | tenancy + Control Plane config | Workspace home, members, envs, secrets | under Org; contains Projects/Agents | shared workspace libraries |
| 4 | **Projects** | Logical grouping of related agents/products | Product Runtime grouping (Epic 7) | Project board, settings | under Workspace | project templates |
| 5 | **Agents** | The configurable agent object (persona/lang/providers/policy) | Agent config (§A0) + **Agent Runtime** (Epic 6) | Agent list, Agent Builder, versions | central hub; uses Knowledge/Tools/Prompts | agent inheritance, A/B agents |
| 6 | **Prompts** | Prompt authoring, library, versioning | **Prompt Runtime** (Epic 5) | Prompt library, editor, diff | consumed by Agents | prompt eval scoring |
| 7 | **Knowledge** | Documents/FAQs/rubrics an agent retrieves | **Knowledge Runtime** (Epic 5) | Knowledge base list, upload, chunk inspector | attached to Agents | GraphRAG/multi-vector (existing seams) |
| 8 | **Memory** | Layered, cross-session caller memory config | **Memory Runtime** (Epic 5) | Memory settings, entity inspector | per Agent/session | returning-caller profiles (§A7) |
| 9 | **Tools** | External capabilities an agent invokes | **Tool Runtime** (Epic 6) | Tool catalog, tool editor, permissions | attached to Agents; approvals | tool marketplace, org-private tools |
| 10 | **MCP** | Connect external MCP servers | **MCP Runtime** (Epic 6) | MCP server list, connect, capability view | surfaces tools into module 9 | MCP directory/registry |
| 11 | **Workflows** | Deterministic multi-step orchestration | **Workflow Runtime** (Epic 6) | Workflow graph editor, runs | used by Agents | visual workflow templates |
| 12 | **Products** | Compose agents+deps into a deployable manifest | **Product Runtime** (Epic 7) | Product manifest editor, compile report | wraps Agents/Tools/Knowledge/Workflows | product marketplace |
| 13 | **Deployments** | Draft→test→approve→deploy→rollback | **Product Runtime** deployment plan (Epic 7) | Deploy view, version history, rollback | consumes Products | canary/blue-green (Epic 7 seams) |
| 14 | **Testing / Evals** | Pre-prod validation, regression, replay-as-test | `evals/` + **Replay** (Epic 4/5) | Test suites, run detail, eval scores | gates Deployments | LLM-judge, dataset mgmt |
| 15 | **Sessions** | Live & historical conversation instances | `event_log` projections (§A5/A7) | Session list, session detail, live view | produced by Deployments | live intervene/whisper |
| 16 | **Analytics** | Usage/latency/cost/quality/business metrics | `metric.*` events + projections | Dashboards, wagon-wheel, funnels | reads Sessions | custom metric builder |
| 17 | **Logs** | Raw typed event stream, searchable | `event_log` (§A5) | Event explorer, filters, export | under Sessions | log streaming/export sinks |
| 18 | **Monitoring** | Health, alerts, SLO tracking, incidents | metrics + Gateway health | Status, alerts, SLO board | reads Analytics/Logs | on-call/paging integrations |
| 19 | **Billing** | Plans, usage metering, invoices, quotas | metering over `metric.*` (new surface) | Plan, usage, invoices, quotas | org-level; gates features | usage-based tiers, credits |
| 20 | **Marketplace** | Templates/agents/prompts/tools/knowledge/integrations | curated `ProductManifest`/Tool registry | Marketplace home, item, publish | feeds Agents/Products/Tools | revenue share, partner program |
| 21 | **Settings** | Org/workspace/user preferences, branding | Control Plane config + tenant settings | Settings surfaces | cross-cutting | white-label/custom domains |
| 22 | **Audit** | Immutable record of who did what | `event_log` = audit log (§A11.2) | Audit explorer, export | reads all modules | SIEM export, retention policy |
| 23 | **Security** | Auth, SSO, MFA, API keys, secrets, compliance | §A11.2 + secrets seam (Tool/MCP) | Security center, keys, SSO, compliance | cross-cutting | SCIM, DLP, key rotation |
| 24 | **AI Product Manager ("Coach")** | Internal assistant that helps build/optimize agents | **composes** existing runtimes (read-only advisor) | Coach panel, suggestions inbox | reads Analytics/Sessions/Products | autonomous optimization (opt-in) |

Modules 1–23 are the standard platform; module 24 is VoxVoice's signature differentiator
(§18). Supporting cross-cuts (**Notifications**, **Support**, **Global Search**,
**AI Assistant surface**) are navigation-level services described in §8, not data owners.

---

# 7. Customer Journey

The canonical path, each step naming the owning capability and the "aha" that must land.

```
Landing → Signup → Org → Workspace → First Agent → Knowledge → Test → Deploy → Monitor → Analyze → Optimize → Production
```

| Step | What the customer does | Owning capability | The "aha" (must be felt) | Failure to avoid |
|---|---|---|---|---|
| **Landing** | Sees value prop, live demo | marketing surface | "It's a real, low-latency voice agent" | vague/black-box promise |
| **Signup** | Email/OAuth/SSO; org auto-created | §A11.2 auth | Under 60s to a workspace | forced sales call |
| **Organization** | Names org, invites, picks region | tenancy | "This is my governed space" | premature complexity |
| **Workspace** | Gets a default `dev` env | tenancy + envs | "Clear place to build" | env confusion |
| **First Agent** | Template or blank → Agent Builder | Agent config + Agent Runtime | "I configured a persona in minutes" | empty-state paralysis |
| **Knowledge** | Uploads a doc; sees it chunked | Knowledge Runtime | "My agent knows my content" | silent ingestion failures |
| **Test** | Talks to it in-browser; sees live events + latency | Replay + event channel | "I can *see* it think, live" (I2) | untestable black box |
| **Deploy** | Promotes to an env; gets number/embed/SDK | Product Runtime deploy | "Reproducible, reversible ship" (Epic 7) | scary irreversible deploy |
| **Monitor** | Watches live sessions, latency, cost | metrics + Sessions | "I know it's healthy, per turn" (I1) | flying blind |
| **Analyze** | Reads dashboards, wagon-wheel, quality | Analytics projections | "I see patterns and outcomes" | vanity metrics |
| **Optimize** | Coach suggests prompt/tool/provider fixes | AI Product Manager (§18) | "It tells me *how* to improve" | dead-end analytics |
| **Production** | Approval-gated prod deploy, SLOs, alerts | deploy + Monitoring | "Governed, observed, at scale" | prod as an afterthought |

Time-to-first-test (signup→hearing the agent) is the North-Star activation metric; the
whole V1 surface optimizes for it.

---

# 8. Navigation Architecture

- **Top navigation:** Org/Workspace switcher · environment selector (dev/staging/prod) ·
  Global Search (⌘K) · Quick Actions · Notifications · Help/Coach · User menu.
- **Side navigation (primary, per workspace):** Overview · Agents · Prompts · Knowledge ·
  Tools · MCP · Workflows · Products · Deployments · Testing · Sessions · Analytics ·
  Logs · Monitoring · Settings. (Org-level: Members · Billing · Security · Audit ·
  Marketplace.)
- **Global Search (⌘K):** unified search across agents, products, sessions, docs,
  settings, and actions ("deploy support-agent to staging"). A command palette, not just
  a search box — GitHub/Notion class.
- **Quick Actions:** New Agent · Upload Knowledge · Run Test · Deploy · Invite Member.
- **Recent Items** and **Favorites/Pins:** persisted per user; agents, sessions,
  dashboards.
- **Breadcrumbs:** Org / Workspace / Project / Agent / Version — mirrors the product
  hierarchy (§10) exactly.
- **Notifications:** deploy status, test results, quota/billing, approvals awaiting you,
  Coach suggestions, incidents. Grouped, filterable, with an inbox.
- **User menu:** profile, theme (dark default — §19), keyboard shortcuts, API keys,
  sign out.

**Principle:** navigation depth never exceeds Org → Workspace → Object → Version →
Detail. Everything reachable in ≤3 clicks or one ⌘K.

---

# 9. Screen Inventory

Grouped by module; classified **V1** (activation-critical), **V2** (scale/governance),
**Future** (differentiation/marketplace). Abbreviated where a group repeats a pattern.

| Module | Screens | Class |
|---|---|---|
| Onboarding | Landing, Signup, Create Org, Create Workspace, Quickstart wizard | V1 |
| Agents | Agent list, **Agent Builder** (persona/language/providers/policy tabs), Version history, Diff, Test panel | V1 |
| Prompts | Prompt library, Prompt editor, Version diff, Attach-to-agent | V1 (library V2) |
| Knowledge | Knowledge base list, Upload/import, **Chunk inspector**, Retrieval test | V1 (retrieval test V2) |
| Memory | Memory settings, Entity inspector, Retention profile | V2 |
| Tools | Tool catalog, Tool editor (schema+adapter binding), Permissions/approval config | V1 (approvals V2) |
| MCP | MCP server list, Connect server, Capability snapshot view | V2 |
| Workflows | Workflow list, **Graph editor**, Run detail | V2 |
| Products | Product manifest editor, **Compile report**, Package view | V1 (package view V2) |
| Deployments | Deploy view, Environment matrix, Version history, **Rollback**, Approval queue | V1 (approvals/canary V2) |
| Testing | Test suites, Test run detail, **Replay-as-test**, Eval scores | V1 (evals V2) |
| Sessions | Session list, **Session detail (transcript+events+latency)**, **Live session view**, **Replay** | V1 |
| Analytics | Overview dashboard, **Wagon-wheel/quality**, Latency, Cost, Funnels, Custom report | V1 core (custom V2) |
| Logs | Event explorer, Saved filters, Export | V1 (export V2) |
| Monitoring | Status board, Alerts, SLO board, Incident view | V2 |
| Billing | Plan & usage, Invoices, Quotas, Payment methods | V1 plan/usage (invoices V2) |
| Marketplace | Marketplace home, Item detail, Publish flow, My listings | Future |
| Settings | Org settings, Workspace settings, User prefs, Branding, White-label/domains | V1 core (branding/white-label Future) |
| Audit | Audit explorer, Export | V2 |
| Security | Security center, API keys, SSO, MFA, Compliance/BAA, Secrets | V1 keys/MFA (SSO/compliance V2) |
| Coach (AI PM) | Coach panel, Suggestions inbox, Guided-build, Optimization report | V2 (autonomous Future) |
| Platform (staff) | Platform health, Flags, Marketplace review, Plans admin | V2 |

---

# 10. Product Hierarchy

```
Organization
   └─ Workspace                 (team/BU; owns environments + secrets)
        └─ Project              (logical grouping)
             └─ Agent           (the config object; §A0)
                  └─ Version     (immutable, content-addressed — Epic 7 identity chain)
                       └─ Product (ProductManifest wrapping the agent + deps)
                            └─ Deployment (to an environment; reversible)
                                 └─ Session (one conversation; event-log rooted)
                                      └─ Turn / Analytics (projections of the event log)
```

**Why this is future-proof.** It is the *customer-facing rendering of hierarchies the
frozen Epics already enforce*: `org_id` tenancy (all Epics), the Epic 7 immutable
identity chain (Manifest→Definition→Compilation→Package→Deployment), and the Epic 5
event-log-as-source-of-truth (Session/Turn/Analytics are projections, §A7). Adding a
level (e.g. **Org Group** above Organization for holding companies, or **Fleet** across
workspaces) is additive — a new grouping node, never a redesign — exactly as §A12
requires. Nothing in this tree introduces a new *owner* of state.

---

# 11. SaaS Architecture

The multi-tenant control surface. Everything below is a customer-facing projection of
capabilities the Constitution and Epics already mandate — no new architecture.

| Concern | Design | Anchored in |
|---|---|---|
| **Organizations** | Top tenant; billing + identity root; one region home | tenancy (all Epics) |
| **Teams / Workspaces** | Membership + RBAC scope + env/secret boundary | §A11.2 org-scoping |
| **Projects** | Logical grouping under a workspace | Epic 7 grouping |
| **Environments** | `dev`/`staging`/`prod` (+ custom); a deployment target profile | Epic 7 `DeploymentProfile`/`DeploymentTarget` |
| **API Keys** | Org-scoped, hashed, rotatable; scoped to role/env | §A11.2 (hashed API credentials) |
| **Secrets** | Never in code/logs/VCS; injected via the Tool/MCP `CredentialProvider` seam | §A11.1.6, Tool/MCP runtime |
| **Usage** | Metered from `metric.*` events; per-org, per-agent, per-env | §A5 metrics pipeline |
| **Billing / Subscriptions** | Plans + usage-based metering (see §15) | new surface over metrics |
| **Quotas** | Feature-gated limits enforced at write time (accepted-or-rejected, §A2.6) | Tool `FeatureGateRegistry`/`RateLimitEngine` precedent |
| **Feature Flags** | Per-plan/per-org gating; deterministic; no silent no-ops | Tool feature-gate pattern |
| **Tenant Settings** | Per-org config (retention, PII policy, defaults) | §A7/§A11.2 |
| **Branding** | Logo/colours on Studio and hosted surfaces | Future |
| **Custom Domains / White-Label** | Per-org domain for hosted agent widgets & Studio | Future (Stage 4) |

**Isolation invariant (restate).** Every query is org-scoped; cross-org access is
structurally impossible, not merely checked — the discipline the Phase-1 audit proved and
every Epic since preserved. White-label and custom domains are additive Stage-4 topology,
never a data-model change.

---

# 12. AI Platform Architecture

The "build a great agent" toolbox — each item mapped to its owning runtime. Studio is the
authoring/observability surface; the runtime is the truth.

| Capability | Studio surface | Owner |
|---|---|---|
| **Prompt Library + Versioning** | Author, version, diff, attach; content-hash identity | **Prompt Runtime** (Epic 5) |
| **Knowledge Base** | Upload/import, chunk inspector, retrieval test | **Knowledge Runtime** (Epic 5) |
| **Memory** | Layered memory config, entity inspector, retention | **Memory Runtime** (Epic 5) |
| **LLM Providers** | Select/failover-chain per agent; capability flags shown | Provider Runtime (Epic 5) |
| **STT Providers** | Select streaming STT; degraded-mode surfaced (§A6.4) | Provider Runtime |
| **TTS Providers** | Select voice/TTS; capability flags | Provider Runtime |
| **Tool Library** | Catalog, schema editor, permissions/approvals | **Tool Runtime** (Epic 6) |
| **MCP Servers** | Connect, view capability snapshot, surface as tools | **MCP Runtime** (Epic 6) |
| **Workflows** | Graph editor, runs | **Workflow Runtime** (Epic 6) |
| **AI Evaluation** | Eval suites, LLM-judge, scores | `evals/` + Reasoning (as judge) |
| **Prompt Testing** | Test a prompt against datasets; regression | Prompt Runtime + evals |
| **Conversation Replay** | Deterministic, visualization-only replay of any session | **Replay Platform** (Epic 4/5) |

**Non-negotiables carried from the Constitution.** Provider selection shows **declared
capability flags** and validates at write time (§A6.1) — you cannot save an agent against
an undeclared capability. Degraded modes (e.g. non-streaming STT) are shown explicitly
(§A6.4). Replay is read-only and deterministic (§A5 persistence). None of this is new
engineering — it is surfacing what Epics 5–6 already own.

---

# 13. Deployment Model

Studio's deploy flow is a thin surface over Epic 7's frozen Product Runtime. Epic 7
already owns manifest→definition→compilation→package→deployment-*plan*; Studio adds the
human workflow and the actuation trigger (a later Epic 8 milestone builds the actuator —
Epic 7 deliberately stops at deployment *intent*).

```
Draft → Compile → Test/Eval gate → Approval (gated) → Deploy → Monitor → (Rollback | Promote)
                                                                     │
                                                              Version History (immutable chain)
```

| Stage | What happens | Owner |
|---|---|---|
| **Draft** | Author agent/product manifest | Studio + Product Runtime authoring |
| **Compile** | Deterministic 8-stage compile → `CompiledProduct` + report | Product Compiler (Epic 7) |
| **Testing** | Test/eval suites must pass to proceed | evals + Testing module |
| **Approval** | Approver signs off (separation of duties); gated per env/policy | RBAC + deploy policy |
| **Deployment** | `DeploymentPlan` → **actuator** (new Epic 8 milestone) applies it | Epic 7 plan + Epic 8 actuator |
| **Rollback** | Same-major rollback rule; deterministic | Epic 7 `VersionManager`/`RollbackPolicy` |
| **Monitoring** | Live health/latency/cost after deploy | metrics + Monitoring module |
| **Version History** | Immutable, content-addressed chain | Epic 7 identity chain |

**The one honest gap, named not hidden.** Epic 7 froze at deployment *intent*
(`DeploymentPlan`) and explicitly never actuates. Studio's "Deploy" button therefore
depends on a **new, additive Deployment Actuator** — an Epic 8 milestone (M8.x) that
consumes a frozen `DeploymentPlan` and drives real infrastructure via adapters, exactly
as the Product Runtime's extension seams anticipate (scheduling/approval/orchestration/
GitOps/K8s/Terraform seams already exist, none actuate). It changes no frozen code.

---

# 14. Analytics

Dashboards are projections of `metric.*` and the `event_log` — never a separate data
store (§A5).

| Dashboard | Key metrics | Source |
|---|---|---|
| **Agent Analytics** | Sessions, completion, escalation, top intents, outcomes | event-log projections |
| **Usage** | Sessions, minutes, tokens, tool calls — per agent/env/org | `metric.*` |
| **Latency** | Per-stage p50/p95: endpoint, STT-final, LLM-first-token, first-audio, **voice-to-voice** | `metric.turn` (I1) |
| **Cost** | Provider cost per session/turn; cost per outcome | cost meter (§A11.1 observability) |
| **Quality** | Interruption rate, silence/awkward gaps, eval scores, sentiment | Turn Intelligence events + evals |
| **Conversation metrics** | Turns/session, talk-ratio, tool success, drop/reconnect | event-log projections |
| **Business metrics** | Bookings, resolutions, qualified leads, interview pass-rate (per consumer) | consumer-defined outcome events |
| **Wagon-wheel / domain views** | Domain-specific outcome maps (e.g. VoxHire scoring) | consumer projections |

**Latency is the hero.** Because the runtime measures per-stage latency on *every* turn
(I1, §A8 budgets), the latency dashboard is a truthful, always-on product feature — the
single clearest proof that VoxVoice is not a black box. Analytics never invents a number
the runtime doesn't emit.

---

# 15. Billing

A usage-metered SaaS model over the existing `metric.*` stream.

| Tier | Audience | Shape |
|---|---|---|
| **Free** | P1 indie / evaluation | Capped minutes/sessions, community support, VoxVoice branding, dev env only |
| **Pro** | P1/P2 startups | Usage-based minutes + seats, all envs, email support, standard providers |
| **Business** | P2/P3/P7 SMB & scaleups | Higher quotas, approvals, SSO-lite, priority support, cost dashboards |
| **Enterprise** | P4/P8/P10/P11 | Custom quotas, SSO/SCIM, private providers, BAA/compliance, audit export, SLA, white-label |

**Mechanics.** **Credits/usage** metered from `metric.*` (minutes, tokens, tool calls);
**invoices** generated per org billing cycle; **limits/quotas** enforced at write time as
feature gates (accepted-or-rejected, §A2.6 — never a silent overage surprise). Cost
transparency is a feature: the Cost dashboard (§14) shows provider cost so customers see
the margin story VoxVoice's provider-portability enables. Metering is a **new
control-plane surface**, not a runtime change — it consumes events the runtime already
emits.

---

# 16. Security

Directly inherits §A11.2 and the secrets discipline proven since Phase 1.

| Concern | Design |
|---|---|
| **Authentication** | Email/password + OAuth; short-lived session tokens; scoped client credentials for callers (§A4.3) |
| **Authorization** | Org-scoped on every query; RBAC (§5); separation of duties on deploy/approval |
| **SSO** | SAML/OIDC (Stage 4/Enterprise); group→role mapping |
| **MFA** | TOTP/WebAuthn; enforceable per org policy |
| **Audit** | The `event_log` **is** the audit log (§A11.2); who/what/when on every mutation and privileged read; exportable |
| **Encryption** | In transit (TLS) and at rest (System of Record + Storage); per-org retention |
| **API Keys** | Org-scoped, hashed, rotatable, role/env-scoped |
| **Secrets** | Never in code/logs/VCS; injected via Tool/MCP `CredentialProvider`; redaction at the logging sink (§A11.1.6) |
| **Compliance** | Per-org retention/export/deletion (data-protection-shaped); PII redaction in transcripts (§A11.2); BAA path for P8; region residency (Stage 4) for P11 |

**Threat model** lives under `docs/security/` (§A11.2) — opened by Epic 8's first security
milestone. No security claim here exceeds what the Constitution already mandates; Studio
surfaces and operationalizes it.

---

# 17. Marketplace (future)

A curated exchange that turns the declarative-artifact model (Epic 7 manifests, Tool
descriptors, Prompt/Knowledge objects) into shareable, installable units.

| Listing type | What it is | Backed by |
|---|---|---|
| **Templates** | Ready-to-fork agents/products for a use case (receptionist, screener, support) | `ProductManifest` |
| **Agents** | Publishable agent configs | Agent config + Product Runtime |
| **Prompts** | Reusable prompt packs | Prompt Runtime objects |
| **Tools** | Installable tool integrations (calendar/CRM/etc.) | Tool descriptors + adapters |
| **Knowledge Packs** | Domain knowledge bases (compliance, product docs) | Knowledge Runtime docs |
| **Integrations** | MCP servers, transport/telephony, analytics sinks | MCP + provider plugins |

**Why it's credible for VoxVoice specifically:** because everything is *already* a
portable, content-addressed, versioned artifact (Epic 7), marketplace items are just
signed, published packages — the `SignatureProvider` seam and package registry already
exist (Epic 7, unsigned by default). Revenue share / partner program are Future. This is
a distribution surface, not new runtime architecture.

---

# 18. AI Product Manager — "Coach"

VoxVoice's signature differentiator: an internal assistant that helps customers *build and
improve* agents. Architecturally, **Coach is a Consumer/advisor that composes existing
runtimes — it owns nothing and, in V2, executes nothing on the customer's behalf without
consent.**

| Capability | What Coach does | How (composed, never new ownership) |
|---|---|---|
| **Help build agents** | Guided, conversational agent creation | Reasoning Runtime over templates + capability catalog |
| **Review prompts** | Flags ambiguity, missing guardrails, length/budget issues | Prompt Runtime analyzer + Reasoning judge |
| **Detect problems** | Surfaces high interruption, latency spikes, failing tools, drop-offs | reads Analytics/Logs projections |
| **Optimize cost** | Suggests provider/model swaps given latency/quality/cost trade-offs | reads Cost/Latency; proposes provider config |
| **Improve conversations** | Recommends prompt/knowledge/turn-policy changes from replays | Replay + evals + Reasoning |
| **Generate workflows** | Proposes a Workflow graph for a described process | Workflow Runtime authoring (proposal only) |
| **Recommend improvements** | Prioritized, actionable suggestion inbox with one-click drafts | composes all of the above |

**Guardrails (constitutional).** Coach reads projections and *proposes* changes as
draftable artifacts; a human (or an explicit, audited approval) applies them. Coach never
becomes a hidden owner of state, never bypasses RBAC/audit, and never edits a frozen
runtime. An optional, opt-in **autonomous optimization** mode (Future) runs Coach's
proposals through the same deploy→approval→rollback path as a human — no privileged path.
This is the Constitution's "own the loop, rent the edges" applied to product management
itself: VoxVoice owns the advisory loop; the LLM is a rented edge.

---

# 19. UI Philosophy

| Principle | Commitment |
|---|---|
| **Consistency** | One design system; the same object looks/behaves the same everywhere; predictable patterns |
| **Minimalism** | Progressive disclosure; defaults that work; depth on demand, never up front |
| **Accessibility** | WCAG 2.2 AA; keyboard-complete; screen-reader labelled; motion-reduce respected (P11 needs this) |
| **Speed** | Optimistic UI, streamed data, ⌘K everywhere; the app must feel as fast as the runtime is |
| **Keyboard-first** | Every primary action has a shortcut; command palette is a first-class navigator |
| **Dark mode** | Dark is the default (developer audience); light fully supported; theme-aware everywhere |
| **Responsive** | Desktop-primary (builder/ops), tablet-capable; hosted agent widgets are mobile-first |
| **Honest states** | Real loading/empty/error states; never a fake metric or a spinner hiding a failure (mirrors Replay's "honest-metrics" rule) |

The UI's job is to make the runtime's owned superpowers — live events, latency truth,
replay, portability — *feel* like superpowers.

---

# 20. Competitive Analysis

Patterns studied for ideas only; **nothing copied** (§A12 discipline). The recurring
opportunity: competitors optimize the *start*; VoxVoice can own the *whole lifecycle*
because it owns the runtime.

| Competitor | Their strength | Their structural gap | VoxVoice opportunity |
|---|---|---|---|
| **Vapi** | Fast voice-agent setup, big provider menu | Orchestrates black-box vendors; limited deep observability/control | Live events + mid-call control + per-stage latency as product features |
| **Retell AI** | Clean voice-agent UX, low-latency claims | Closed engine; you don't own the loop or portability | Owned, portable, replayable runtime; export your agent |
| **Bland AI** | Telephony scale, self-serve | Opaque internals; trust/observability concerns | Observability + audit + deterministic replay as trust |
| **ElevenLabs** | Best-in-class TTS + agents surface | Voice-model-centric; you're anchored to their stack | Provider-neutral: their voice as *one plugin*, not the platform |
| **OpenAI (Realtime)** | Frontier models, one-vendor simplicity | Single-vendor lock-in; app must build the platform | Multi-provider platform *around* any model, incl. theirs |
| **Microsoft Copilot Studio** | Enterprise reach, M365 integration | Heavy, ecosystem-locked, voice is secondary | Voice-first, lightweight, portable, developer-loved |
| **Google Vertex AI** | Infra depth, model breadth | Low-level; not a voice-agent product | Opinionated voice product on open infra |
| **LangGraph Studio** | Great agent-graph dev UX | Framework/dev-tool, not a managed voice runtime/product | Managed runtime + product + ops, not just a graph IDE |

**One-line positioning.** *"Vapi's speed with GitHub's ownership and a Stripe-grade
dashboard — on a runtime you actually control."*

---

# 21. Product Roadmap

| Release | Theme | Headline scope |
|---|---|---|
| **V1 (Epic 8 core)** | **Activate: idea → tested → deployed** | Onboarding, Agent Builder, Knowledge, Tools (core), Products/Deployments over Epic 7, **Deployment Actuator (M8.x)**, in-browser Test + live events, Sessions/Replay, core Analytics (incl. Latency), Logs, API keys/MFA, Free/Pro billing |
| **V1.5** | **Trust & governance** | Approvals + separation of duties, Monitoring/alerts/SLO, Audit explorer, quotas/feature-gates, Cost dashboard, Business tier |
| **V2** | **Depth & intelligence** | Workflows graph editor, MCP module, Memory config, Evals/LLM-judge, **Coach (AI PM)** V2, custom reports, SSO, Enterprise tier |
| **V3** | **Ecosystem & scale** | Marketplace (templates/tools/knowledge), white-label/custom domains, multi-region (Stage 4), autonomous Coach (opt-in), partner/revenue-share |
| **Long-term (5–10y)** | **Sport-/domain-agnostic multimodal platform** | New capability *kinds* (speech-to-speech, vision/video via §A12 additive contracts), cross-session caller identity, marketplace network effects — all additive on the frozen core |

Sequencing rule: **V1 is ruthlessly scoped to activation** (time-to-first-test). Every
later release adds governance, then intelligence, then ecosystem — never re-architecting
the core.

---

# 22. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Scope explosion** — 24 modules is a multi-year surface for a solo/small team | High | High | Ruthless V1 cut (§21); modules 15/13/05/14 first; the rest gated behind demand |
| R2 | **Deployment Actuator is real infra work** not yet built (Epic 7 stopped at intent) | High | High | Named explicitly (§13); build as an additive Epic 8 milestone on existing Epic 7 seams; no frozen change |
| R3 | **Studio accidentally becoming an owner of runtime state** (constitutional violation) | Medium | Critical | ADR-0021: Studio is Consumer + management surface only; CI/boundary review; it reads projections, writes manifests/config, calls Control Plane |
| R4 | **Coach over-promises / acts without consent** | Medium | High | Advisor-only in V2; all actions via the same deploy/approval/audit path (§18); opt-in autonomy only |
| R5 | **Billing/metering drift from real usage** | Medium | High | Meter from the authoritative `metric.*` stream; reconcile against event log; no parallel counters |
| R6 | **Provider-portability promise breaks** if a Studio form encodes vendor specifics | Medium | High | Forms bind to *capability flags* (§A6), never vendors; write-time validation |
| R7 | **Enterprise features (SSO/SCIM/residency) pull the roadmap early** | Medium | Medium | Keep them V2/V3 & additive (Stage 4 topology); don't let one deal re-architect the core |
| R8 | **UX debt** — a powerful runtime behind a mediocre console loses to slicker black boxes | Medium | High | Design system + §19 discipline from V1; observability made *felt*, not just present |

---

# 23. Future Opportunities

- **Multimodal & new capability kinds** — speech-to-speech, vision, video — land as new
  §A6 capability contracts + registry categories; Studio gains modules, the core doesn't
  change (§A12).
- **Cross-session caller identity & memory** — returning-caller profiles (§A7 designed-for
  layer) unlock CRM-grade personalization as a product feature.
- **Sport-/domain-agnostic expansion** — the same runtime + Studio serves any spoken-
  interaction domain (support, healthcare, education, screening) via templates and
  knowledge packs, not new engines.
- **Marketplace network effects** — a portable-artifact ecosystem (Epic 7 packages) is a
  defensible moat competitors with private data models can't easily copy.
- **Autonomous Coach** — opt-in, governed self-optimization; VoxVoice becomes a platform
  that *improves customers' agents while they sleep*, safely.
- **Developer platform & SDKs** — first-class API/SDK/CLI parity with Studio (Epic 5's
  closure certification already lists SDK/Developer-Platform as buildable on existing
  seams).
- **Telephony & omnichannel** — SIP/PSTN as a Real-time Transport plugin (§A6/§A12),
  surfaced as a "buy a number" flow.

---

# 24. Final Recommendation

**Adopt this blueprint as the master reference for Epic 8 and every downstream customer-
platform Epic, and proceed to implementation planning at M8.1.** Specifically:

1. **Ratify ADR-0021** — *"The Customer Platform (VoxVoice Studio) is a management &
   authoring surface plus a Constitution-§A0 Consumer; it owns no runtime state, reads
   only event-log projections, writes only manifests/agent-config, and reaches the runtime
   only through the Control Plane, Product Runtime, and existing seams."* This is the
   invariant that keeps the entire platform surface constitutional. (Recommended, not
   ratified here — this is a design milestone.)
2. **Recommend supporting ADRs** for later ratification: ADR-0022 *Deployment Actuator as
   an additive layer over the frozen Epic 7 `DeploymentPlan`*; ADR-0023 *Billing/metering
   consumes the authoritative `metric.*` stream, never a parallel counter*; ADR-0024
   *Coach is an advisor that composes runtimes and acts only via the standard
   deploy/approval/audit path*. Each is additive and touches no frozen Epic.
3. **Sequence V1 for activation** (§21): Agents → Products/Deployments (+Actuator) →
   Sessions/Replay → Testing → core Analytics → Knowledge/Tools → billing/keys. Everything
   else is demand-gated.
4. **Hold the line on ownership** (R3/R6): Studio forms bind to capability flags, not
   vendors; every mutation is org-scoped and audited; the runtime stays frozen.

**Bottom line.** VoxVoice already owns the expensive, defensible thing — the runtime.
Epic 8 is not another moonshot; it is the disciplined act of *exposing that ownership as a
world-class product surface*. Built on the existing seams, contradicting nothing frozen,
and led by the one differentiator no black-box competitor can match — a runtime the
customer truly owns, observes, replays, and improves — VoxVoice Studio is the surface that
turns three frozen Epics into a business.

---

*Design-only milestone (M8.0A). No code, no APIs, no database tables, no changes to any
frozen Epic. This document is the master blueprint for every future Epic 8 milestone and
is subordinate to the Engineering Constitution and all ratified ADRs. Recommendations
(ADR-0021…0024) await formal ratification per the ADR workflow before any implementation
begins.*
