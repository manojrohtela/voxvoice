# ADR-0031 — The Billing Platform lives permanently in `platform/billing/`, a self-contained top-level plane that imports only `core`

| | |
|---|---|
| **Status** | **Accepted 2026-08-01** (ratified at M9.1A). Named as an **M9.2 kickoff entry criterion** by the [Epic 9 Implementation Plan](../roadmap/epic9-implementation-plan.md) (M9.1, §16 Repository Impact) and by [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md) §4 (Topology). This milestone ratifies it. |
| **Amends** | Introduces the Epic 9 **Billing Platform** as a new top-level package `platform/billing/`, peer to `product/` (Epic 7) and `actuator/` (Epic 8). Amends no Constitution Part-A/Part-B section, no frozen Epic, and **no ownership boundary** — ADR-0023 already fixed *what* Billing owns; this ADR fixes *where it lives*. Required by CODING_STANDARDS §2 (a new top-level directory requires an ADR), per the ADR-0018 / ADR-0022 precedent. |
| **Numbering** | **ADR-0024 remains reserved** for the Coach-autonomy decision recommended by ADR-0021. **ADR-0025–ADR-0030 remain reserved** for the recommended Epic 9 architecture batch ([Blueprint](../architecture/epic9/billing-platform-blueprint.md) §26: entitlement mechanics, money/ledger discipline, payment/tax edges, critical-path prohibition, commerce/licensing reuse, billing-account hierarchy). This structural placement record therefore takes **ADR-0031** rather than consuming a reserved number. |
| **Deciders** | Technical Owner |
| **Related** | [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md) (Billing owns revenue and nothing else — the ownership boundary this ADR gives a physical home), [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) (new top-level `platform/product/`, one-way `product → …`), [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) (Studio owns no runtime state; `Studio → Platform` one-way), [ADR-0022](ADR-0022-deployment-actuator-runtime-actuation.md) (new top-level `platform/actuator/`, `actuator → product` read-only), [ADR-0002](ADR-0002-substrate-behind-contracts.md) (substrate behind owned contracts), [ADR-0006](ADR-0006-single-system-of-record.md), [ADR-0009](ADR-0009-workflow-durable-repository-exception.md), [ADR-0016](ADR-0016-loop-engine-no-registrable-identity.md) / [ADR-0020](ADR-0020-registry-nouns-reserved-for-product-registry.md) (vocabulary discipline), [Epic 9 Blueprint](../architecture/epic9/billing-platform-blueprint.md) (M9.0A), [Billing Provider Strategy](../architecture/epic9/billing-provider-strategy.md) (M9.0C), [Epic 9 Implementation Plan](../roadmap/epic9-implementation-plan.md) (M9.1); Constitution §A0, §A1, §A2, §A2.4, §A2.5, §A4 (two-plane), §A5, §A6, §A7, §A9, §A10, §A11.1, §A11.2, §A11.3, §A12, §A14 (repository structure); ARCHITECTURE_RULES AR-15 (acyclic graph), AR-30 (producers never know consumers), AR-50 (one responsibility per module); CODING_STANDARDS §2; Epic 5/6/7/8 Freeze Manifests; Repository Baseline v3.0.0 |

## Status

**Accepted 2026-08-01**, ratified at milestone **M9.1A**. It **authorizes no code**. It fixes the
repository topology every Epic 9 implementation milestone (M9.2–M9.26) must follow, so that **no
future milestone revisits package placement and no package movement is expected**. Epics 5, 6, 7,
and 8 remain frozen and byte-for-byte unchanged.

**Amended 2026-08-03 at M9.4** — two clarifications, recorded here rather than left as drift
(DL-0114, `docs/architecture/epic9/billing-persistence-layer.md` §11). The **decision** — the plane
lives at `platform/billing/`, imports `core` + stdlib, and is composed only at the control plane —
is untouched.

| # | Amends | Change | Why |
|---|---|---|---|
| **A1** | §10 (composition-root sketch, "Persistence → `platform/api/persistence/` *(additive)*") | Billing persistence is implemented at **`platform/billing/persistence/`**. | **IR3 forbids `billing` importing `api`.** Storage inside `api/` would either invert that edge or split the plane across two top-level packages — and §17 names *"extract Billing into its own service"* as a deployment decision this topology must preserve, which a plane whose storage lives in another plane's package does not. The **plane's** placement is unchanged; this is the §13 latitude over internal structure, stated explicitly because §10 had named a location. |
| **A2** | §11 IR1 | New **IR1a**: `billing/persistence/**` may additionally import `sqlmodel` / `sqlalchemy`. No other billing module may. | Storage cannot be implemented under "core + stdlib only". The exception is **scoped to one sub-package** and has its own assertion; the M9.2 boundary test was **tightened into per-layer checks**, not relaxed, so `billing/foundation` and `billing/application` keep IR1 at full strength and are asserted to contain no mention of a session, a table, or SQL. No vendor SDK is involved, so **IR5 is untouched**. |

**Amended again 2026-08-04 at M9.5** (DL-0115, `docs/architecture/epic9/billing-control-plane-api.md` §12):

| # | Amends | Change | Why |
|---|---|---|---|
| **A3** | §11 IR1 | New **IR1b**: `billing/customer/**` may additionally import `fastapi`, `starlette`, and `pydantic`. No other billing module may. | An HTTP transport cannot be implemented under "core + stdlib only". Same shape as IR1a: **scoped to one sub-package**, with its own assertion, leaving IR1 at full strength for `foundation` and `application` — both asserted to import no web framework and to be unaware the API exists. Three further M9.2 boundary assertions (ambient-clock, IR9 `metric.*`, M9.2 module scope) were **tightened into per-layer checks rather than relaxed**, and two new assertions added, so the boundary suite is stricter after M9.5 than before it. |

Note on §10: the API is an **additive package inside the Billing plane** (`billing/customer/`),
consistent with amendment A1 — and it is a **separate ASGI application**, never mounted inside the
Epic 8 Control Plane, because ADR-0023 §4 gives revenue a distinct failure domain. CR2 is
unaffected: Billing remains absent from `platform/bootstrap.py`.

---

## 1. Executive Summary

ADR-0023 ratified that the Billing Platform is a new top-level plane owning revenue and nothing else.
It deliberately did not say *where in the repository that plane lives* — a structural question that
CODING_STANDARDS §2 requires an ADR to answer before a new top-level directory exists, exactly as
ADR-0018 answered it for `platform/product/` and ADR-0022 for `platform/actuator/`.

**This ADR answers it, permanently:**

> **The Billing Platform lives in `platform/billing/` — a self-contained top-level package, peer to
> `product/` and `actuator/`, that imports `core` and the standard library only, with exactly one
> capped exception (`billing/refs.py`, read-only Epic 7 identity references, mirroring
> `actuator/planning.py`).**
>
> Its **control-plane surface** — foundation service, application use cases, persistence, routers,
> and Studio screens — lands **additively inside the existing Epic 8 layers**, which import
> `billing` and are never imported by it.
>
> Everything Billing needs from another plane (organization facts, seat counts, `metric.*` events,
> Actuator status) arrives by **injection through neutral ports**, not by import. That single choice
> is what makes the dependency graph acyclic by construction rather than by discipline.

Six options were evaluated. `platform/billing/` (Option A) is selected. `platform/revenue/` and
`platform/commercial/` are rejected on **ubiquitous-language** grounds (ADR-0023 named the plane
"Billing"; `billing.*` is the ratified event namespace; "revenue" is what it *owns*, not what it
*is*). Nesting under `api/`, `product/`, or `actuator/` is rejected because each would place a plane
inside another plane's ownership, break a freeze, or invert a ratified dependency direction.

---

## 2. Context

**What the repository already establishes.** The platform has a settled, ADR-governed topology:

| Precedent | Shape | Governing ADR |
|---|---|---|
| `platform/core/` | The shared kernel: contracts, events, states, errors, ids, ports, policies, FSM, module substrate. Imports the standard library only. | Constitution §A14, M1/M5.0 |
| `platform/runtime/`, `providers/`, `events/`, `gateway/`, `replay/`, `telemetry/`, `tenant/`, `observability/` | Runtime plane + edge packages, each importing downward only | Epic 2–5 |
| `platform/product/` | New top-level plane for the declarative Product Runtime; `product → core[, …]` one-way | **ADR-0018** |
| `platform/actuator/` | New top-level plane for deployment execution; `actuator → product` **read-only**, confined to a single module (`actuator/planning.py`); **the Epic 8 Foundation imports only `actuator`, never `product`** | **ADR-0022**, DL-0104 |
| `platform/api/` | The Epic 8 Customer Platform: `foundation/` → `application/` → `persistence/` → routers + `customer/` (Control Plane) + `studio/` | Epic 8 Freeze |
| `platform/bootstrap.py` | The **runtime** composition root (DL-0010) | M2C.2 |
| `platform/api/bootstrap.py` + `api/customer/container.py` | The **control-plane** composition root | Epic 8 |

**Two lessons this repository has already paid for**, both of which shape this ADR:

1. **DL-0104 (Actuator).** The Epic 8 Foundation's import-boundary test forbids importing a frozen
   engine. That is why `actuator/planning.py` became *the only* place Epic 7's `DeploymentPlan` is
   reused, and why the Foundation imports **only** `actuator`. The same discipline applies verbatim
   to Billing.
2. **DL-0011 / DL-0020 (dependency inversion).** Vestigial second seams and cross-plane imports were
   removed in favour of a single provision mechanism and `core/ports` dependency inversion. A new
   plane that imports four other planes would re-introduce exactly what those milestones removed.

**What Epic 9 needs a home for.** The Implementation Plan (M9.1) defines 14 bounded contexts across
25 milestones and 7 streams, including a provider-adapter family that must stay vendor-free
(M9.0C AD3) and offline-deterministic by default (AD7). Without a ratified topology, streams B1–B4
would each make a placement decision, and the first cross-stream merge would be a refactor.

---

## 3. Problem Statement

**Where does the Billing Platform physically live, and what may it import?**

Five sub-questions must each have one permanent answer:

1. **Top-level plane or nested package?** ADR-0023 ratified "new top-level plane" as an *architectural*
   statement; CODING_STANDARDS §2 still requires a *structural* record before the directory exists.
2. **What is it called?** Naming is not cosmetic here: ADR-0020 established that nouns are reserved
   and load-bearing, and `billing.*` is already the ratified event namespace of ADR-0023.
3. **How does it obtain facts it does not own?** Organization identity, seat counts, `metric.*`, and
   deployment status all live in frozen planes. Importing them would create four dependency edges
   into a new plane and make every future frozen-Epic change a Billing concern.
4. **Where does the customer-facing surface live?** Billing needs a control-plane API and Studio
   screens, both of which belong to a frozen Epic 8 whose layering must be extended additively, not
   forked.
5. **What is public and what is internal?** Without a declared public surface, external code will
   reach into billing submodules and the plane will lose the ability to evolve internally — the
   failure ADR-0002 and ADR-0016 exist to prevent elsewhere.

Left unanswered, the default outcome is a billing package that grows imports into four planes, a
customer surface split across two layerings, and a placement ADR revisited at every milestone.

---

## 4. Architectural Options

| Option | Placement | Assessment |
|---|---|---|
| **A** | **`platform/billing/`** | Top-level plane, peer of `product/` and `actuator/`. Matches ADR-0023's ratified topology, the ADR-0018/0022 precedent, and the ratified `billing.*` vocabulary. |
| **B** | `platform/revenue/` | Structurally identical to A; **names the plane after what it owns rather than what it is**. Diverges from ADR-0023's own title and from the `billing.*` event namespace, creating two words for one concept. |
| **C** | `platform/commercial/` | Structurally identical to A; vaguer still — "commercial" names a *category*, not a component, and would compete with "catalog"/"commerce" nouns already used *inside* the plane (Commercial Catalog, Commerce Engine). |
| **D** | Nested under `platform/api/` | Places a plane inside the **frozen** Customer Platform. Breaks the Epic 8 freeze, merges two failure domains and two retention regimes, and contradicts ADR-0023 §4 (Billing is not a Customer-Platform module). |
| **E** | Nested under `platform/product/` | Places revenue inside the **frozen** declarative Product Runtime, which ADR-0018 fixed as a composition layer that owns no execution and no state. Inverts `product → …` and breaks the Epic 7 freeze. |
| **F** | Nested under `platform/actuator/` | Places revenue inside deployment **execution**. Violates AR-50 (one responsibility) at the plane level and ADR-0022's single-ownership statement; deployment billing is one meter, not a reason to merge planes. |
| **G** | Alternative topology — a separate repository, or a `platform/billing/` split across multiple top-level packages (e.g. `billing/` + `metering/` + `ledger/`) | **Separate repository**: premature — it forfeits the shared kernel, the single test suite, and the atomic boundary tests that make the freeze verifiable, for an isolation benefit a deployment topology already provides (§A9). **Multiple top-level packages**: three new top-level directories where one suffices; metering, rating, and the ledger are *layers of one bounded plane*, not peers, and splitting them would put an internal seam on a repository boundary. |

---

## 5. Decision

**Ratify Option A.**

1. **The Billing Platform lives permanently in `platform/billing/`** — a new top-level package, peer
   to `core/`, `product/`, `actuator/`, `api/`, and the runtime packages. This is the physical home
   of the plane ADR-0023 ratified.
2. **`platform/billing/` is self-contained.** It imports **`platform/core/` and the standard library
   only**, with exactly one capped exception (§11): `billing/refs.py` may import `platform/product/`
   read-only for Epic 7 identity/reference types, mirroring `actuator/planning.py` (ADR-0022,
   DL-0104).
3. **Billing imports no other plane.** Not `api/`, not `actuator/`, not `runtime/`, not the Execution
   Platform (`agent/`, `tool/`, `skill/`, `workflow/`, `mcp/`, `loop/`), not `provider/`/`providers/`,
   not `memory/`, `knowledge/`, `context/`, `prompt/`, `reasoning/`, `pipeline/`, not `events/`,
   `gateway/`, `replay/`, `observability/`.
4. **Everything Billing consumes arrives by injection through neutral ports**, defined inside
   `billing/` and implemented on the *other* side of the boundary: organization/seat facts, the
   `metric.*` stream (subscribed via the `core.events` contract, as `gateway/` does), Actuator
   status, and the clock. **Consumption is not an import.**
5. **The control-plane surface is additive inside Epic 8's existing layers** — a foundation service,
   application use cases, persistence, routers, and Studio screens — each importing `billing` and
   never imported by it. This is exactly how the Actuator was surfaced (DL-0104).
6. **Provider adapters live inside the plane**, at `billing/adapters/`, not in Epic 8's frozen
   `api/adapters/`.
7. **Composition happens only at the control-plane composition root.** Billing is **never wired into
   the runtime composition root** (`platform/bootstrap.py`) — that would place revenue on the runtime
   path, which ADR-0023 forbids.
8. **The public surface is declared and narrow** (§12); everything else is internal (§13).

**Naming rationale (binding).** The plane is called **billing**, not revenue and not commercial,
because ADR-0023 titles it the *Billing Platform*, `billing.*` is its ratified event namespace, and
the Constitution's vocabulary discipline (ADR-0020's reserved-noun precedent) requires one word per
concept. "Revenue" remains the correct word for **what Billing owns** (the Revenue Ledger, revenue
state, revenue recognition) and is used precisely that way inside the plane — which is exactly why it
must not also name the plane.

---

## 6. Repository Topology

```
platform/
├── core/                # shared kernel (stdlib only) — contracts, events, states, errors,
│                        # ids, ports, policies, fsm, module            [existing, unchanged]
├── runtime/ providers/ events/ gateway/ replay/ telemetry/ tenant/
├── memory/ knowledge/ context/ prompt/ reasoning/ provider/ pipeline/  [Epic 5, frozen]
├── agent/ tool/ skill/ workflow/ mcp/ loop/ hooks/ integration/ bridge/[Epic 6, frozen]
├── product/             # Product Runtime — declarative              [Epic 7, frozen, ADR-0018]
├── actuator/            # Deployment execution                       [Epic 8, frozen, ADR-0022]
├── api/                 # Customer Platform + Control Plane + Studio [Epic 8, frozen]
│   ├── foundation/      #   ← + billing foundation service           (additive, Epic 9)
│   ├── application/     #   ← + billing use cases                    (additive, Epic 9)
│   ├── persistence/     #   ← + billing persistence                  (additive, Epic 9)
│   ├── routers/         #   ← + billing router                       (additive, Epic 9)
│   ├── customer/studio/ #   ← + billing screens                      (additive, Epic 9)
│   ├── adapters/        #   Epic 8 provider binding — NOT billing's adapters
│   └── bootstrap.py     #   control-plane composition root           ← Billing is wired HERE
├── billing/             # ★ THE BILLING PLATFORM                     [Epic 9, NEW — this ADR]
├── observability/
├── bootstrap.py         # runtime composition root — Billing is NEVER wired here
└── tests/               #   ← + billing suites                       (additive, Epic 9)
```

**Peer status is the point.** `billing/` sits beside `product/` and `actuator/` — three planes, three
ownership domains, three ADRs, one shared kernel. No plane is inside another.

---

## 7. Package Layout

Inside `platform/billing/`. **This is topology, not implementation** — no module content, no
schemas, no repositories, no routers are defined here; only where each of the M9.1 bounded contexts
belongs, so that no milestone has to decide.

```
platform/billing/
├── __init__.py            # THE PUBLIC SURFACE (§12) — facade + published contracts, nothing else
│
├── domain.py              # shared billing value objects (money, currency, period, identifiers)
├── lifecycle.py           # pure FSM transition tables for every billing lifecycle
├── policies.py            # declared, validated policy objects (rounding, discount order, dunning,
│                          #   late-arrival, retry) — no business rules hardcoded elsewhere
├── events.py              # the `billing.*` event vocabulary (§A5 envelope, produced only)
├── errors.py              # typed billing errors rooted in core.errors
├── ports.py               # NEUTRAL INBOUND PORTS — the facts Billing consumes but never imports:
│                          #   organization/seat facts, usage-event source, deployment status, clock
├── seams.py               # persistence + outbound seam declarations (contracts only)
├── refs.py                # ⚠ THE ONLY module permitted to import `product` (read-only Epic 7
│                          #   identity/reference types) — the `actuator/planning.py` precedent
│
├── accounts/              # 1  Billing Account + hierarchy                  (M9.5)
├── catalog/               # 2  Commercial Catalog (plans, tiers, features, meter definitions) (M9.6)
├── pricing/               # 3  Price books, versions, shapes, effective dating (M9.7)
├── subscription/          # 4  Subscription lifecycle, terms, proration     (M9.8)
├── entitlement/           # 5  Grants + immutable snapshots                 (M9.9)
├── metering/              # 6  Ingestion, attribution, usage records, aggregation (M9.10)
├── rating/                # 7  Charge lines, discounts, coupons, credits application (M9.11)
├── ledger/                # 8  Revenue ledger, credit ledger, recognition   (M9.12)
├── invoicing/             # 9  Billing run, invoices, credit notes, numbering (M9.13)
├── payments/              # 10 Payment lifecycle, dunning, reconciliation   (M9.14)
├── tax/                   # 11 Tax profiles, determinations, evidence       (M9.15)
├── refunds/               # 12 Refunds, approvals, disputes                 (M9.16)
├── commerce/              # 13 Marketplace orders, listings' terms, payables (M9.20)
├── licensing/             # 14 Contracts, committed spend, licenses, channel grants (M9.21)
│
└── adapters/              # RENTED EDGES (M9.0C) — one contract per family, many implementations
    ├── payment/           #   contract + deterministic offline fake + vendor adapters
    ├── tax/               #   contract + deterministic offline fake + vendor adapters
    ├── document/          #   render / deliver / e-invoicing formats
    ├── export/            #   accounting / ERP export
    └── registry.py        #   capability-flagged adapter registration (the §A6 pattern)
```

**Placement rules that follow from this layout:**

| Rule | Statement |
|---|---|
| **PL1** | **One bounded context, one sub-package.** The 14 M9.1 contexts map 1:1 onto the 14 domain sub-packages. A new commercial domain is a new sub-package, never a new top-level directory. |
| **PL2** | **Pure things live at the top.** `domain`, `lifecycle`, `policies`, `events`, `errors` are shared, dependency-free, and importable by every sub-package. |
| **PL3** | **Sub-packages do not import each other freely.** The commercial lifecycle is a DAG: `accounts → catalog → pricing → subscription → entitlement`, `metering → rating → ledger → invoicing → {payments, tax} → refunds`, `commerce`/`licensing` above both. A sub-package imports only from earlier in the DAG plus the shared top-level modules. |
| **PL4** | **Adapters are leaves.** `adapters/*` imports the shared top-level modules and its own contract only — never a domain sub-package. Domains reach adapters through `seams.py`/`registry.py`, never by importing a vendor implementation. |
| **PL5** | **`refs.py` is capped.** Read-only identity/reference use of Epic 7 artifacts only. No behaviour, no state, no second module. |
| **PL6** | **No new top-level directory** is expected for Epic 9 beyond `platform/billing/`. If one is ever proposed, it requires a superseding ADR. |

---

## 8. Layer Structure

Billing spans two homes, deliberately: the **plane** (domain, self-contained) and its **surface**
(additive, inside Epic 8). This is the Actuator shape, generalized.

| Layer | Home | Role | Milestone |
|---|---|---|---|
| **Domain / plane** | `platform/billing/` | All commercial logic, lifecycles, policies, adapters, ports, seams. Knows nothing about HTTP, persistence technology, Studio, or any other plane. | M9.2, M9.5–M9.16, M9.20, M9.21 |
| **Foundation** | `platform/api/foundation/` *(additive)* | The org-scoped billing service: authorizes via **existing** RBAC, resolves tenancy, mirrors `billing.*` into the durable Audit Platform, and supplies Billing's inbound ports with Customer-Platform facts. **Imports only `billing`** — never a frozen engine, never `product`. | M9.2/M9.3 |
| **Application** | `platform/api/application/` *(additive)* | Billing commands/queries; one unit of work per mutation. | M9.3 |
| **Persistence** | `platform/api/persistence/` *(additive)* | Durable storage for billing aggregates behind `billing/seams.py`; append-only enforcement; continues the existing migration chain. | M9.4 |
| **Customer API** | `platform/api/routers/` + `api/customer/` *(additive)* | Thin org-scoped transport for billing projections and intents. Adds no logic. | M9.18 |
| **Studio** | `platform/api/customer/studio/` *(additive)* | Billing screens; owns no state (ADR-0021). | M9.19 |
| **Tests** | `platform/tests/` *(additive)* | Billing suites + extensions to the existing boundary/certification suites. | every milestone |

**The load-bearing constraint, restated from DL-0104:** the Epic 8 Foundation's import-boundary test
forbids importing a frozen engine. Therefore **`api/foundation` imports `billing` and nothing else
new** — every Epic 7 reference Billing needs is reached through `billing/refs.py`, inside the plane.

---

## 9. Dependency Direction

**Frozen, one-way, acyclic (AR-15):**

```
                    api/foundation ─► api/application ─► api/persistence
                          │
                          ▼
                      billing/  ──────► core/            (+ stdlib)
                          │
                          └──► product/                  (READ-ONLY, `billing/refs.py` ONLY)

   injected inbound (never imported):  organization & seat facts · `metric.*` event source ·
                                       deployment status · clock
   outbound (contracts):               payment · tax · document · export adapters
```

| # | Rule |
|---|---|
| **DD1** | `billing → core` (and stdlib). Nothing else, except DD2. |
| **DD2** | `billing/refs.py → product`, **read-only**, identity/reference types only. Capped to that one module (PL5). |
| **DD3** | **`api → billing`**, never the reverse. The Epic 8 layers import the plane; the plane never imports the layers. |
| **DD4** | **No frozen Epic (5/6/7/8) may import `billing`.** Extends the existing one-way freeze assertions. |
| **DD5** | **`billing` imports no runtime, execution, provider, deployment, gateway, replay, or observability package.** Facts from those planes arrive by injection (DD6). |
| **DD6** | **Consumption is injection, not import.** Billing declares inbound ports in `billing/ports.py`; the composition root supplies implementations. The `metric.*` subscription uses the `core.events` contract — the same discipline that lets `gateway/` stream runtime events while importing `core` only. |
| **DD7** | **No cycles**, verified by the existing AST/import-boundary suite, extended per milestone. |
| **DD8** | Within the plane, sub-package imports follow the commercial-lifecycle DAG (PL3); adapters are leaves (PL4). |

---

## 10. Composition Root

| # | Rule |
|---|---|
| **CR1** | **Billing is composed only at the control-plane composition root** — `platform/api/bootstrap.py` / `api/customer/container.py`, extended additively, exactly as the Actuator's execution repository was threaded at M8.16. |
| **CR2** | **Billing is never wired into `platform/bootstrap.py`** (the runtime composition root, DL-0010). Wiring revenue into the runtime would place Billing on the conversation path — forbidden by ADR-0023 §Decision 4/9. This is the single most important composition rule in this ADR. |
| **CR3** | **The plane never self-wires.** `billing/` constructs no adapter, opens no connection, reads no environment variable, and creates no clock. Every dependency is injected — the DI discipline established at M2C.2/DL-0010 and consolidated at DL-0011 (one provision mechanism, not two). |
| **CR4** | **Adapters are selected by configuration at the composition root**, registered through `billing/adapters/registry.py` with capability flags (§A6). Swapping a provider is a composition change, never a domain change. |
| **CR5** | **The default composition outside production is fully offline and deterministic** — offline payment/tax/document/export fakes and a fixed clock (M9.0C AD7; the Epic 8 `EchoTransport` / ADR-0022 Local-adapter precedent). Production composition is opt-in and validated at startup (§A2.6). |
| **CR6** | **Secrets come from the existing credential seam** (Epic 8 `CredentialProvider` posture); Billing introduces no new secret mechanism (M9.0C AD8). |
| **CR7** | **The event subscription is composed, not imported** — the concrete bus is injected as a `core.events.EventBus`; Billing never imports `events/`. |

---

## 11. Import Rules

Binding, and mechanically checkable.

| # | Rule | Verified by |
|---|---|---|
| **IR1** | `platform/billing/**` may import: `platform.core.*`, the standard library, and `platform.billing.*`. | AST scan |
| **IR2** | `platform/billing/refs.py` may **additionally** import `platform.product.*`, read-only. **No other billing module may import `product`.** | AST scan (module allow-list of exactly one) |
| **IR3** | `platform/billing/**` may **never** import: `api`, `actuator`, `runtime`, `providers`, `provider`, `agent`, `tool`, `skill`, `workflow`, `mcp`, `loop`, `hooks`, `integration`, `bridge`, `memory`, `knowledge`, `context`, `prompt`, `reasoning`, `pipeline`, `events`, `gateway`, `replay`, `observability`, `telemetry`, `tenant`, `studio`. | AST scan (deny-list) |
| **IR4** | **No frozen-Epic package may import `billing`.** | AST scan (extends the existing one-way freeze assertion) |
| **IR5** | **No vendor SDK or vendor type may appear outside `platform/billing/adapters/<family>/`** — the same confinement that keeps `pipecat` inside `runtime/pipeline/` (ADR-0002) and vendor types out of `core`. | AST scan + review (M9.0C AD3) |
| **IR6** | **`api/foundation` imports `billing` only** — it gains no new import of `product` or any frozen engine (DL-0104 boundary preserved). | existing foundation boundary test |
| **IR7** | Within `billing/`, sub-package imports follow the commercial-lifecycle DAG; **`adapters/*` imports no domain sub-package**. | AST scan |
| **IR8** | **External code imports the public surface only** (§12) — no external module imports a `billing` submodule directly. | AST scan |
| **IR9** | **Billing emits no `metric.*`** (it produces `billing.*` and `metric.billing.*` only) — the ADR-0023 M8 rule, checked at the import/emission boundary. | AST scan + review |

---

## 12. Public Surface

**`platform/billing/__init__.py` is the plane's entire public surface.** It exports exactly four
categories, and nothing else:

| # | Exported | Why it is public |
|---|---|---|
| **PS1** | **The billing facade** — the single entry object the control-plane Foundation drives | One seam in, mirroring the Actuator facade |
| **PS2** | **Inbound port contracts** (`billing/ports.py`) — organization/seat facts, usage-event source, deployment status, clock | The composition root must implement them |
| **PS3** | **Adapter contracts + the adapter registry** (payment, tax, document, export) | Provider implementations and composition depend on them (§A6) |
| **PS4** | **Published read contracts** — the **entitlement snapshot** shape, projection shapes, the `billing.*` event vocabulary, and typed errors | Enforcing planes, Studio projections, and observability consume them |

**Explicitly not public:** every domain sub-package, every aggregate, every lifecycle table, every
policy object, `refs.py`, `seams.py`, and every adapter implementation. External code that needs
something internal is a signal to extend the facade or publish a contract — never to deepen an
import (IR8).

**Stability.** The public surface is versioned and extended **additively** (§A11.4). The entitlement
snapshot in particular is versioned and immutable (ADR-0023 §11.2), because four planes will consume
it from M9.17.

---

## 13. Internal Packages

Everything under `platform/billing/` other than the §12 exports is **internal and freely
refactorable** without an ADR, provided the public surface and the import rules hold:

- the 14 domain sub-packages and all their aggregates, engines, and lifecycles;
- `domain.py`, `lifecycle.py`, `policies.py`, `errors.py` internals;
- `seams.py` implementations and persistence details;
- `refs.py`;
- every adapter implementation behind its contract.

**Why this matters:** it is what lets M9.5–M9.21 reshape internals as the domain is learned, without
a placement or interface ADR each time. The freeze this ADR creates is **topological, not internal**.

---

## 14. Extension Points

Declared now so future work is additive by construction (§A12) and never a placement decision:

| Extension | Where it lands | Requires an ADR? |
|---|---|---|
| **A new payment / tax / document / export provider** | a new implementation under `billing/adapters/<family>/` + one registry entry | No |
| **A new adapter *family*** (e.g. payout rails, fraud signals) | a new sub-package under `billing/adapters/` | No |
| **A new commercial domain** (e.g. revenue-share programs, rebates) | a new sub-package under `billing/` per PL1 | No |
| **A new meter, plan, price shape, coupon shape, currency, jurisdiction** | configuration/data over `catalog/`, `pricing/`, `tax/` | No |
| **Merchant-of-record channel** (M9.0C §16) | a channel-scoped settlement path in `payments/` + ledger facts | No |
| **Reseller / OEM / white-label** | `accounts/` hierarchy + `pricing/` scoped price books + `entitlement/` composition | No |
| **A third-party rating/invoicing engine adopted behind the seams** | an adapter family + composition change; ownership unchanged | No |
| **A new inbound fact Billing must consume** | a new port in `billing/ports.py` + a composition-root implementation — **never a new import** | No |
| **A second billing deployment topology** (billing as a separately deployed service) | composition + deployment concern (§A9); the package boundary already supports it | No |
| **Moving `billing/` or splitting it into multiple top-level packages** | — | **Yes — superseding ADR** |
| **Any new top-level directory for Epic 9** | — | **Yes — superseding ADR** |

---

## 15. Testing Strategy

Topology exists to make testing cheap; these are the placement consequences (the full strategy is
Implementation Plan §12).

| # | Rule |
|---|---|
| **T1** | **Billing suites live in `platform/tests/`**, additively, alongside the existing repository-wide suite — one suite, one green bar, no separate runner. |
| **T2** | **The plane is testable with zero infrastructure**: `billing/` imports only `core`, so every domain sub-package is unit-testable in isolation with injected ports. |
| **T3** | **Offline deterministic adapters are the default in every environment except production** (CR5). The billing suite **opens no socket, calls no vendor, and moves no money**. |
| **T4** | **Ports are the test seams.** Organization facts, usage events, deployment status, and the clock are injected, so metering, rating, and subscription behaviour are exercised against fixtures — not against Epic 5/8 runtimes. |
| **T5** | **Boundary tests are part of the topology**, not an afterthought: IR1–IR9 are executable assertions, extended at every milestone (the Epic 8 `test_ga_certification` pattern). |
| **T6** | **Adapter contract suites run against every implementation** of a family, including the offline fake — so a new provider proves itself against the same contract (§A6). |
| **T7** | **A total-Billing-outage drill** proves conversations, deployments, and Studio are unaffected — a placement-level guarantee, since Billing is absent from the runtime composition root (CR2). |

---

## 16. Migration Impact

**None on existing code.** This ADR creates a directory; it moves nothing.

| Area | Impact |
|---|---|
| Epics 5/6/7/8 packages | **Zero change.** Byte-for-byte unchanged; verified per milestone. |
| `platform/api/*` | **Additive only** — new modules inside existing layers, no edits to existing services (the M8.16/M8.17 pattern). |
| `platform/core/` | **No change required by this ADR.** Any future core addition would be additive and justified separately. |
| Migration chain | Continues additively from the existing chain at M9.4; chain validity is a DoD line. |
| Composition roots | `api/bootstrap.py` extended additively; `platform/bootstrap.py` **untouched** (CR2). |
| Packaging/build | One new package under the existing distribution; no build-system restructuring. |
| Existing tests | No modification; new suites added; boundary suites extended. |
| Documentation | New `docs/architecture/epic9/*` component specs; INDEX/CHANGELOG/DECISION_LOG updated. |

---

## 17. Future Growth

The topology is chosen to absorb Epic 9's full commercial ambition and beyond **without moving a
package**:

- **14 → N domains**: new commercial domains are sub-packages (PL1). The plane has room for revenue
  share, rebates, entitlement marketplaces, and usage forecasting without a structural change.
- **1 → N providers, 1 → N markets**: adapter families and registry entries (§14).
- **Self-serve → enterprise → channel**: contracts, licensing, reseller, and white-label are already
  placed (`licensing/`, `accounts/`, `pricing/`, `entitlement/`).
- **Monolith → service**: because `billing/` imports only `core` and receives everything else by
  injection, extracting it into a separately deployed service is a **deployment-topology** decision
  (§A9), not a re-architecture — the same property ADR-0021 noted for future ownership decomposition.
- **Build → buy, later**: a third-party engine adopted behind the adapter seams changes composition,
  not placement (M9.0C).

**What would require a superseding ADR:** moving `billing/`, splitting it across top-level packages,
adding a second Epic 9 top-level directory, importing a plane instead of injecting it, or wiring
Billing into the runtime composition root.

---

## 18. Alternatives Considered

1. **Option B — `platform/revenue/`.** **Rejected.** Structurally identical to A, so it turns on
   vocabulary — and vocabulary is load-bearing in this repository (ADR-0020 reserved the "Registry"
   nouns precisely to stop two words meaning one thing). ADR-0023 names the plane the **Billing
   Platform**; `billing.*` is its ratified event namespace; the Blueprint, Provider Strategy, and
   Implementation Plan all say "Billing". Naming the directory `revenue/` would give the plane two
   names and, worse, would collide conceptually with the **Revenue Ledger** *inside* it — "revenue"
   is what Billing owns, not what Billing is.

2. **Option C — `platform/commercial/`.** **Rejected**, more strongly than B. "Commercial" names a
   category rather than a component, and the plane already contains a **Commercial Catalog** and a
   **Commerce** domain — a directory named `commercial/` containing `catalog/` and `commerce/` is an
   ambiguity generator. It also breaks the established one-word-per-plane convention (`product`,
   `actuator`, `billing`).

3. **Option D — nested under `platform/api/`.** **Rejected.** Epic 8 is **frozen**; nesting a new
   plane inside it either breaks the freeze or subordinates revenue to the Customer Platform's
   layering. ADR-0023 §4 explicitly rejected "Billing as a Customer-Platform module": revenue has a
   distinct failure domain, retention obligation, and compliance surface, and its records outlive the
   customer record (RS4). It would also put revenue behind the customer API's availability envelope
   and violate AR-50 at the plane level.

4. **Option E — nested under `platform/product/`.** **Rejected.** ADR-0018 fixed Product Runtime as a
   **declarative** composition layer that owns no execution and no durable domain state; Epic 7 is
   frozen. Placing a stateful revenue plane inside it breaks the freeze, contradicts ADR-0018's
   characterization, and inverts the `product → …` direction (Billing would then be imported by, and
   entangled with, a frozen declarative layer).

5. **Option F — nested under `platform/actuator/`.** **Rejected.** ADR-0022 made the Actuator the sole
   owner of deployment **execution** — one responsibility (AR-50). Deployment is *one meter* among a
   dozen in the Blueprint's meter set; that is a reason to consume its events, not to merge planes.
   Nesting would also make every deployment change a billing change and vice versa.

6. **Option G(i) — a separate repository.** **Rejected as premature.** It forfeits the shared kernel,
   the single repository-wide green bar, and — decisively — the **atomic boundary tests** that make
   the freeze verifiable in one AST pass. The isolation it offers is already available at the
   deployment layer (§A9), and this ADR's import discipline preserves the option (§17) at zero cost.

7. **Option G(ii) — multiple Epic 9 top-level packages** (`billing/` + `metering/` + `ledger/`).
   **Rejected.** Metering, rating, and the ledger are **stages of one bounded plane**, not peers;
   splitting them would place an internal seam on a repository boundary, triple the placement
   surface, and require three ADRs where one suffices. The sub-package layout (§7) already gives the
   separation with none of the cost.

8. **Option A — `platform/billing/`, self-contained, importing `core` only.** **Accepted.** It is the
   only option that simultaneously satisfies ADR-0023's ratified topology, the ADR-0018/0022
   precedent, the Epic 5–8 freezes, AR-15 (acyclic), AR-50 (one responsibility), the ratified
   vocabulary, and the Implementation Plan's stream partitioning.

---

## 19. Trade-offs

**Benefits.**

- **Every milestone knows where code belongs** — 14 contexts, 14 sub-packages, one adapter tree, one
  additive surface. Placement is never re-litigated (the success criterion of this milestone).
- **The freeze holds by construction.** Billing cannot break Epics 5–8 because it imports none of
  them, and none of them can import it.
- **Acyclic by construction, not by discipline.** Consumption-by-injection means the graph cannot
  develop a cycle even under schedule pressure.
- **Maximum testability.** A plane that imports only `core` is unit-testable everywhere, offline, with
  no infrastructure.
- **Stream parallelism is structural.** B1 (domains), B2 (metering/rating), B3 (money/documents),
  B4 (adapters), and B5 (surface) touch different directories — the anti-merge-conflict mechanism the
  Implementation Plan depends on.
- **Future service extraction stays free** (§17).

**Costs, stated plainly.**

- **Injection is more ceremony than importing.** Every consumed fact needs a port and a composition
  wiring. That is the price of the acyclic guarantee, and it is paid once per fact.
- **Two homes for one feature.** A user-visible billing capability touches `billing/` *and* additive
  modules in `api/*` — the same split the Actuator has, and the same one that keeps the frozen
  Foundation boundary intact.
- **`refs.py` is a deliberate, capped compromise.** A single read-only import edge to `product` is
  allowed rather than re-declaring Epic 7 identity types. It is capped to one module precisely
  because uncapped it would become the seam through which the plane's isolation erodes.
- **A large plane in one package.** 14 sub-packages plus adapters is a big directory; the internal DAG
  (PL3/PL4) and the narrow public surface (§12) are what keep it navigable.

---

## 20. Compliance Verification

1. **Mandatory on every Epic 9 milestone.** Any change that places billing code outside
   `platform/billing/` or its declared additive surface, or that adds an import forbidden by §11, is
   **non-compliant**.
2. **IR1–IR9 are executable assertions**, extended per milestone in the existing AST/import-boundary
   suite (the same mechanism proving `Studio → Platform`, `actuator → product` read-only, *"no Epic 8
   layer imports a frozen engine"*, and *"no frozen Epic imports Epic 8"*).
3. **`refs.py` is the only `product` importer** — verified by an allow-list of exactly one module.
4. **No vendor type outside `billing/adapters/<family>/`** — verified by AST scan (IR5).
5. **`api/foundation` imports `billing` only** and gains no frozen-engine import (IR6, DL-0104
   boundary).
6. **Billing is absent from `platform/bootstrap.py`** — verified, and re-verified by the
   total-Billing-outage drill (T7). This is the placement-level expression of ADR-0023's
   never-blocks-the-runtime constraint.
7. **Frozen Epics remain byte-for-byte unchanged** — verified per milestone (no tracked frozen-Epic
   source modified).
8. **Public-surface discipline**: no external module imports a `billing` submodule (IR8).
9. **The offline deterministic composition is the default outside production** (CR5) — verified by the
   suite opening no socket.
10. **Any deviation requires a superseding ADR.** Placement is frozen; a milestone may not move a
    package by note, comment, or convenience (authority hierarchy, §A11.1.2).

---

## 21. Documentation Updates

Updated in the same change-set:

- **ADR Register** — [adr/README.md](README.md): ADR-0031 added; the ADR-0024 and ADR-0025–0030
  reservations restated.
- **Documentation index** — [INDEX.md](../INDEX.md): ADR table row added.
- **CHANGELOG** — [CHANGELOG.md](../../CHANGELOG.md): Epic 9 M9.1A entry.
- **Decision log** — rationale recorded as **DL-0111**.
- **Implementation Plan** — [roadmap/epic9-implementation-plan.md](../roadmap/epic9-implementation-plan.md)
  §16's "structural placement ADR required as an M9.2 entry criterion" is now **satisfied**; the M9.2
  entry criterion is met.

No frozen document is modified. ADR-0018, ADR-0021, ADR-0022, and ADR-0023 are **referenced and
followed, not amended**.

---

## 22. Repository Conventions

Binding conventions this ADR fixes for Epic 9, so they are never re-decided:

| # | Convention |
|---|---|
| **RC1** | **One plane, one top-level package, one ADR.** `core/` · `product/` (ADR-0018) · `actuator/` (ADR-0022) · `billing/` (this ADR). |
| **RC2** | **One word per plane, matching its ratified vocabulary and event namespace.** `billing/` ⇄ `billing.*`. |
| **RC3** | **A plane imports the shared kernel and nothing else**, except where an ADR names a single capped read-only module (`actuator/planning.py`; `billing/refs.py`). |
| **RC4** | **Consumption is injection.** Cross-plane facts arrive through ports declared by the consumer and implemented at the composition root. |
| **RC5** | **The customer-facing surface of any new plane is additive inside the Epic 8 layers**, and those layers import the plane — never the reverse. |
| **RC6** | **Vendor code is confined to an adapter directory**; no vendor type crosses a seam. |
| **RC7** | **Every plane declares a narrow public surface in its `__init__`;** everything else is internal and refactorable without an ADR. |
| **RC8** | **Offline deterministic implementations are the default outside production**, so the repository-wide suite stays hermetic. |
| **RC9** | **New top-level directory ⇒ ADR** (CODING_STANDARDS §2), permanently. |

---

## 23. Final Recommendation

**Ratify ADR-0031 and begin M9.2 under it.**

The decision is deliberately unglamorous — a directory name and an import rule — but it is the point
at which three prior ADRs either hold or quietly erode. Three provisions carry that weight:

1. **`billing/` imports `core` only.** This single line is what makes the acyclic graph structural
   rather than aspirational, keeps Epics 5–8 frozen by construction, and preserves the option to
   extract Billing into its own service later at no cost.
2. **Consumption is injection, not import.** Every fact Billing needs from another plane arrives
   through a port. Under schedule pressure the tempting shortcut is always "just import the
   foundation service" — and that shortcut is the one this ADR exists to foreclose.
3. **Billing is absent from the runtime composition root.** Placement is where ADR-0023's
   never-blocks-the-runtime guarantee becomes physically true: a plane that is not wired into the
   runtime cannot be on its critical path, whatever a future contributor writes.

After this ADR, every Epic 9 milestone knows exactly where its code belongs, no future placement ADR
is needed, no package movement is expected, and the repository topology for Epic 9 is **frozen**.

---

## 24. ADR Ratification

**ADR-0031 is ratified as Accepted, 2026-08-01, at milestone M9.1A.**

- **Decision:** the Billing Platform lives permanently at **`platform/billing/`** — a self-contained
  top-level plane importing `platform/core/` and the standard library only, with one capped read-only
  exception (`billing/refs.py` → `product`), surfaced additively through the Epic 8 layers, composed
  only at the control-plane composition root, and never wired into the runtime.
- **Binding on:** every Epic 9 milestone, M9.2 through M9.26.
- **Authorizes:** no code. It creates a topology, not an implementation.
- **Freezes:** Epic 9 repository placement. Deviation requires a **superseding ADR**, not a local
  exception (§A11.1.2).
- **Preserves:** ADR-0018, ADR-0021, ADR-0022, and ADR-0023 unchanged; Constitution Part A and Part B
  unamended; Epics 5, 6, 7, and 8 frozen and byte-for-byte unchanged.
- **Numbering:** **ADR-0024** remains reserved for Coach; **ADR-0025–ADR-0030** remain reserved for
  the recommended Epic 9 architecture batch.
- **Entry criterion satisfied:** the Implementation Plan's M9.2 kickoff condition (§16) is met.
  **M9.2 Foundation may begin.**
