# ADR-0021 — VoxVoice Studio is a Consumer and Management Surface; it owns no runtime state

| | |
|---|---|
| **Status** | **Accepted 2026-07-27** (ratified at M8.0B). Proposed in the [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md) (M8.0A); this milestone ratifies it. |
| **Amends** | Introduces Epic 8 (Customer Platform) atop the frozen Runtime Platform v1.0 (Epic 5), Execution Platform v1.0 (Epic 6), and Product Runtime v1.0 (Epic 7). Amends no Constitution Part-A/Part-B section; records the load-bearing ownership boundary for the customer-facing surface. |
| **Deciders** | Technical Owner |
| **Related** | [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md), [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) (Product Runtime = declarative composition layer), [ADR-0019](ADR-0019-binding-resolution-against-registry-snapshot.md) (bindings resolve against an injected immutable snapshot), [ADR-0020](ADR-0020-registry-nouns-reserved-for-product-registry.md) (Registry nouns reserved for the Product Registry), Constitution §A0 (Consumer/Caller glossary), §A1 (the runtime is the product), §A2.5 (one writer per fact), §A4 (two-plane architecture), §A5 (event architecture), §A7 (memory/projections), §A11.2 (org-scoped security), §A12 (additive extension), ARCHITECTURE_RULES AR-15 (acyclic graph) / AR-50 (one responsibility per module), Epic 6 & Epic 7 Freeze Manifests |

## Status

**Accepted 2026-07-27**, ratified at milestone **M8.0B**. The Epic 8 Product Blueprint
(M8.0A) *recommended* this decision as "Proposed, not ratified" because a design
milestone cannot ratify. This ADR milestone is the ratification act named in its own
mandate ("This milestone ratifies the architectural ownership model for VoxVoice
Studio"). To avoid a documentation defect — a ratification milestone leaving its own ADR
marked "Proposed" — the Status is recorded as **Accepted**, with the Proposed→Accepted
lineage stated explicitly above. It authorizes no code; it fixes the boundary every
future Epic 8 implementation milestone must honour.

## Context

**Why Studio exists.** Epics 5–7 built and froze the product itself: a provider-agnostic
voice runtime (Epic 5), an execution platform of tools/skills/workflows/agents/MCP
(Epic 6), and a declarative Product Runtime that compiles a manifest into a deployable
package (Epic 7). The Constitution's first principle is unambiguous: **the runtime is the
product** (§A1). What the platform lacks is the *surface* humans use to operate it — the
console where a customer signs up, builds an agent, connects knowledge and tools, tests,
deploys, monitors, and improves. The Product Blueprint (M8.0A) designs that surface as
**VoxVoice Studio**.

**The decision that must be settled before any Epic 8 code lands.** A customer SaaS
application is under constant pressure to become an owner of state — to keep its own
"session table", its own analytics store, its own billing ledger, its own copy of
deployment status, "for convenience" or "for speed". Every such choice would create a
**second writer for a fact the platform already owns**, and the Constitution forbids it:

- **§A2.5 — One writer per fact.** *"Every piece of state has exactly one owning
  component; all others learn of it via events."* A Studio-owned session/analytics/billing
  store is, by definition, a second writer (or a divergent read-copy presented as truth)
  for facts owned by the Runtime Platform, the event log, and a future Billing Runtime.
- **§A1 / §A0.** Studio is a **Consumer** ("an application built on VoxVoice… configures
  agents, starts sessions, receives events") — not a new plane, not a new runtime. Making
  it an owner would contradict the glossary and the product thesis.
- **Epic 5/6/7 freezes.** Runtime, Execution, and Product state are owned by frozen
  packages. A Studio that mutated any of them directly would break the freeze (Epic 6/7
  Freeze Manifests) and invert the dependency direction ADR-0018 fixed.

So the question — *does Studio own platform state, or is it only a Consumer and management
surface over the existing runtimes?* — has one constitutional answer, ratified here.

## Decision

**Ratify: VoxVoice Studio is a Consumer (§A0) and a Management Surface. It owns no runtime
state.**

Studio **owns only**:

- the **UI** and its component system;
- **user workflows** (guided builders, wizards, review flows);
- the **authoring experience** (editing manifests, prompts, agent configuration);
- the **management experience** (members, workspaces, settings, navigation);
- **orchestration requests** (issuing control commands / deploy requests to the Control
  Plane — requesting an action, never owning its resulting state);
- **presentation** and **projections** (read-only views derived from the event log and
  platform APIs);
- **navigation** (search, breadcrumbs, favorites, quick actions).

Studio **does not own** (each remains owned by its frozen/authoritative component):

- runtime **memory**, **conversation** state, **session** state, **execution** state;
- **products**, **deployment** state;
- **analytics source data**, **billing source data**;
- **provider** state.

Studio holds none of these as a source of truth. Where it displays them, it displays a
**projection** it does not own; where it changes them, it does so by **requesting** the
owning component to act (write an authoring artifact, or issue a Control-Plane command),
never by mutating the owner's state directly.

This makes Studio architecturally identical to any other Consumer (VoxHire, a
sales/support agent) — with elevated, first-party **management scope** and RBAC, but the
same ownership posture.

## Ownership Mapping

Every Product Blueprint module maps to its authoritative owner. "Owner" = the single
component that writes the fact (§A2.5); Studio is the Consumer/surface for all of them.

| Blueprint module | Authoritative owner | Studio's role |
|---|---|---|
| **Platform** | Control Plane + platform config (System of Record) | admin surface (Super Admin) |
| **Organizations** | Control Plane / System-of-Record **tenancy** (`org_id`, all Epics) | manage members/settings; render |
| **Workspaces / Projects** | Control Plane tenancy + Product Runtime grouping (Epic 7) | author metadata; render |
| **Agents** | **Agent Runtime** (Epic 6) + Agent config object (§A0) | author config; request; render |
| **Prompts** | **Prompt Runtime** (Epic 5) | author/version; render |
| **Knowledge** | **Knowledge Runtime** (Epic 5) | submit import; inspect (projection) |
| **Memory** | **Memory Runtime** (Epic 5) | configure; inspect (projection) |
| **Tools** | **Tool Runtime** (Epic 6) | author descriptor; configure; render |
| **MCP** | **MCP Runtime** (Epic 6) | request connect; render capability snapshot |
| **Workflows** | **Workflow Runtime** (Epic 6) | author graph; render runs |
| **Products** | **Product Runtime** (Epic 7) — `ProductManifest`/`CompiledProduct`/`ProductPackage` | author manifest; render compile report |
| **Deployments** | **Product Runtime** `DeploymentPlan` (Epic 7) + future **Deployment Actuator** (Epic 8) | submit deploy request; render status |
| **Testing / Evals** | `evals/` + **Replay Platform** (Epic 4/5) | request runs; render results |
| **Sessions** | **Event Log** projection (§A5/§A7 — runtime is the writer) | render; request control commands |
| **Analytics** | **Event Log** projection (`metric.*`, §A5) | render dashboards |
| **Logs** | **Event Log** (§A5) | render/search/export (read-only) |
| **Monitoring** | metrics + Gateway health (projections) | render; alert config |
| **Billing** | **Billing Runtime (future)** metering the authoritative `metric.*` stream | render plan/usage; manage plan intent |
| **Marketplace** | Product Registry / Tool registry (Epic 7 packages) | browse/publish requests; render |
| **Settings** | Control Plane config + tenant settings | author preferences |
| **Audit** | **Event Log = audit log** (§A11.2 — runtime is the writer) | render/export (read-only) |
| **Security** | §A11.2 auth + secrets seam (Tool/MCP `CredentialProvider`) | manage keys/SSO/MFA intent; render |
| **AI Product Manager ("Coach")** | **Consumer/advisor** composing the above (owns nothing) | compose; propose draftable artifacts |
| **Studio itself** | **Consumer** (this ADR) | owns only UI/workflows/projections |

## Allowed Writes

Studio may write **only** the following (authoring artifacts and requests):

- `ProductManifest` (and its declarative sub-artifacts)
- Prompt (Prompt Runtime authoring input)
- Configuration (agent/workspace/environment configuration values)
- Agent Definition (the §A0 configuration object)
- Deployment **Request** (a request to deploy a `DeploymentPlan` — not the resulting
  deployment state)
- User Preferences
- Workspace Metadata

**Nothing else.** Every allowed write is either a declarative artifact the owning runtime
consumes, or a request the Control Plane authorizes and acts on. Each write is org-scoped
(§A11.2) and recorded in the audit log.

## Forbidden Writes

Studio may **never** directly mutate:

- Execution State
- Conversation State
- Memory (Memory Runtime state)
- Knowledge Runtime State
- Deployment Runtime State (the actual deployed-system state, as opposed to a deploy
  *request*)
- Billing Ledger
- Analytics Events (the `event_log` / `metric.*` stream — Studio never writes events; the
  runtime is the sole producer, §A5)
- Session State
- Provider State

Any of these changes only as a downstream effect of the owning component acting on a
Studio *request* — never by Studio reaching into the owner's store.

## Interaction Model

All interaction flows **downward**, request-in / events-and-projections-out:

```
Studio (Consumer + Management Surface)
   │  authoring artifacts + control/deploy requests   ▲  live events + read-only projections
   ▼                                                  │
Control Plane (stateless: auth · config · session admission · control · event delivery)
   │
   ├─► Product Runtime (Epic 7)      — manifest → definition → compilation → package → DeploymentPlan
   │        │
   │        └─► (Epic 8) Deployment Actuator — applies a frozen DeploymentPlan (additive; ADR-0022 recommended)
   │
   └─► Execution Platform (Epic 6)   — tools · skills · workflows · agents · MCP · loop
            │
            └─► Runtime Platform (Epic 5) — memory · knowledge · context · prompt · reasoning · provider · sessions
                     │
                     └─► Event Log / metric.* — the single source of truth Studio projects and Billing meters
```

**No reverse ownership.** No platform component ever calls up into, imports, or depends on
Studio. Studio subscribes to events and reads projections; it does not become an
authority the platform consults.

## Dependency Direction

**Frozen: `Studio → Platform`, one-way.**

- Studio depends on the platform (Control Plane API, event channel, Product Runtime
  artifacts, projections).
- The platform **never** depends on Studio. No Epic 5/6/7 package, no Control Plane
  service, and no future runtime may import, call, or require Studio.
- The graph stays acyclic (AR-15), exactly as ADR-0018 fixed `product → …` one-way and the
  Epic 6/7 freezes fixed their capability graphs. Studio is a pure downstream Consumer —
  a leaf, never a hub.

## Consequences

**Benefits.**
- The freeze is preserved by construction: Studio cannot break Epic 5/6/7 because it
  cannot write their state or be imported by them.
- One writer per fact holds (§A2.5): sessions/analytics/audit/billing have exactly one
  source of truth; Studio shows projections that cannot silently diverge (§A7 —
  projections of one event log).
- Provider portability (I3) is protected: Studio authors capability-flag-bound config,
  not vendor state.
- Studio is independently buildable, testable, and replaceable — a second Consumer (or a
  third-party console) could be built on the same surface with no platform change.
- Enterprise posture is inherited, not rebuilt: org-scoping, RBAC, and audit already exist
  (§A11.2); Studio surfaces them.

**Trade-offs.**
- Studio cannot take "just cache it locally as truth" shortcuts; it must read projections
  and issue requests, which is more indirection than a monolithic dashboard.
- Some UX (e.g. instant optimistic state) must be modelled as optimistic *projection* over
  an authoritative event, not as Studio-owned truth.
- A **Deployment Actuator** is required (Epic 7 froze at deployment *intent*), named as a
  new additive Epic 8 milestone rather than smuggled into Studio (recommended ADR-0022).

**Future extensibility.**
- New modules are additive Consumer surfaces over new/existing owners (§A12) — never a new
  owner inside Studio.
- A **Billing Runtime** (future) becomes the authoritative owner Studio's Billing module
  projects; until it exists, Studio must not invent a billing ledger of its own
  (recommended ADR-0023: metering consumes the authoritative `metric.*` stream).
- Coach (the AI Product Manager) stays an advisor composing runtimes; opt-in autonomy runs
  through the same request/approval/audit path, never a privileged one (recommended
  ADR-0024).

**Risks.**
- The chief risk is erosion: a future contributor adding a Studio-owned store "for
  convenience". Mitigated by the Compliance Rules below, org-scoped-query and
  import-boundary review, and this ADR being mandatory on every Epic 8 milestone.

## Alternatives Considered

1. **Studio owns everything (a self-contained SaaS monolith with its own session,
   analytics, deployment, and billing stores).** **Rejected.** It creates a second writer
   for facts the platform already owns (§A2.5 violation), forces read-copies that diverge
   from the event log (contradicting §A7's "projections of the same event log cannot
   diverge"), breaks the Epic 5/6/7 freezes if it mutates their state, and destroys
   provider portability the moment its forms encode vendor specifics. It also reduces the
   Constitution's "runtime is the product" (§A1) to "dashboard is the product" — inverting
   the entire thesis.

2. **Microservices per module (an Organization service, a Sessions service, a Billing
   service… each owning its slice, with Studio orchestrating them).** **Discussed, not
   adopted now.** Decomposing *ownership* further is a legitimate Stage-3/4 scaling move
   (§A9) and is compatible with this ADR — each such service would simply be a new
   *authoritative owner* Studio projects, and the interaction/dependency direction here is
   unchanged. But it is a **deployment-topology** decision, not a reason to make Studio an
   owner, and premature decomposition adds operational cost a solo/small team cannot yet
   justify (Blueprint risk R1). This ADR neither mandates nor forbids it; it fixes only
   that **Studio is never one of those owners**.

3. **Current proposal — Studio is a Consumer + Management Surface owning only
   UI/workflows/authoring/projections/requests.** **Accepted.** It is the only option
   consistent with §A2.5 (one writer per fact), §A0/§A1 (Consumer vocabulary; runtime is
   the product), the Epic 5/6/7 freezes, and ADR-0018's one-way dependency direction. It
   preserves portability, observability, and the freeze by construction, and it keeps
   Studio independently buildable and replaceable.

## Compliance Rules

1. **Mandatory on every Epic 8 milestone.** Every future Epic 8 implementation must comply
   with this ownership model. A design or PR that gives Studio ownership of any Forbidden
   Write, or that adds a platform→Studio dependency, is non-compliant.
2. **Allowed/Forbidden Writes are exhaustive.** Studio writes only the Allowed Writes
   list; it mutates none of the Forbidden Writes list directly. New writes require this
   ADR to be superseded or extended, not a local exception.
3. **One-way dependency is enforced.** The `Studio → Platform`, never `Platform → Studio`
   direction is checked the same way the existing import-boundary/AST tests enforce
   `product → core[, …]` — extended to Studio when its package lands.
4. **Any violation requires a superseding ADR.** No Epic 8 milestone may deviate from this
   model by note, comment, or convenience. Deviation is a constitutional change and needs
   a new ADR against this one (authority hierarchy; §A11.1.2).

## Validation

- **Constitution:** consistent with §A0 (Consumer glossary), §A1 (runtime is the product),
  §A2.5 (one writer per fact — the decision's spine), §A4 (two-plane; Studio is a
  Consumer, not a third plane), §A5/§A7 (event log + projections; Studio never writes
  events), §A11.2 (org-scoped security inherited), §A12 (additive-only extension). No
  Part-A/Part-B amendment is made.
- **ADR-0018:** consistent — Studio sits *above* the Product Runtime and depends downward;
  it never inverts the `product → …` direction and never makes Epic 6/7 depend on it.
- **ADR-0019 / ADR-0020:** no conflict — those govern the compiler's `BindingSnapshot` and
  the reserved "Registry" nouns *inside* Product Runtime; this ADR governs the Studio↔
  platform boundary *above* Product Runtime and touches neither type nor term.
- **Epic 5–7 ownership:** no conflict — every owner in the Ownership Mapping remains the
  frozen/authoritative component it already is; Studio adds a surface, subtracts no
  ownership, and imports no frozen package's state.

## Documentation

Updated in the same change-set: the ADR Register ([adr/README.md](README.md)), the
documentation index ([INDEX.md](../INDEX.md)), and the [CHANGELOG](../../CHANGELOG.md);
cross-referenced with the [Epic 8 Product Blueprint](../architecture/epic8/product-blueprint.md).
Rationale recorded as **DL-0087**.
