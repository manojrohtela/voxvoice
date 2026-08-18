# Commercial Catalog — Component Specification (M9.6)

**Status:** 🟢 Implemented · **Date:** 2026-08-04 · **Milestone:** M9.6
**Packages:** `billing/foundation/{products,catalog_rules,catalog_factories}.py` ·
`billing/application/catalog.py` · `billing/persistence/catalog_{models,repositories}.py` ·
`billing/customer/{catalog_schemas.py,routers/catalog_admin.py,studio/}`
**Authority:** Engineering Constitution v2 §A2.4, §A2.6, §A5, §A11.2, §A12 · ADR-0021 · ADR-0023 · ADR-0031
**Rationale:** DL-0116

---

## 1. Executive Summary

The Commercial Catalog is the domain that answers **"what do we sell?"** — products, editions,
plans, features, feature groups, bundles, and the commercial metadata around them — and
nothing else.

The organizing rule is a boundary, stated once and enforced everywhere:

> **The Catalog owns commercial products. It owns no price, no subscription, no invoice, no
> entitlement, and no usage.**

Not one catalog type carries a `Money`, a currency, an amount, or a rate. A test asserts it
across all eight modules, because "we simply did not add a price field" is a property that
decays the moment nobody is checking. A plan says *what a customer gets*; a price book (M9.7)
says what they pay, and the two version independently on purpose — repricing must not require
republishing the product, and correcting a marketing headline must not touch revenue.

Every existing layer was **reused, not redesigned**: the same Foundation discipline, the same
CQRS buses and Unit of Work, the same persistence machinery, the same transport rules. The
catalog is additive throughout — without its repositories wired, the Application is exactly
what M9.3 assembled.

---

## 2. Components Implemented

| Layer | Added | Reused unchanged |
|---|---|---|
| Foundation | `products.py` (9 aggregates, 6 enums, the lifecycle FSM), `catalog_rules.py` (validator, bundle resolver, lifecycle service, search, assembler, visibility), `catalog_factories.py` (`CatalogFactory` + 6 repository contracts) | diagnostics, events, errors, seams, `Plan`/`Feature`/`MeterDefinition` |
| Application | `catalog.py` — 15 commands, 11 queries, 8 view DTOs, 2 use-case classes, wiring table | buses, Unit of Work, authorization orchestrator, event vocabulary |
| Persistence | `catalog_models.py` (6 tables), `catalog_repositories.py` (6 implementations), migration **0016** | `_Repo`, codec, mapping, `BillingDatabase`, composition root |
| API | `catalog_schemas.py`, `routers/catalog_admin.py` (**21 endpoints**) | auth, container, middleware, error translation, OpenAPI |
| Studio | `studio/index.html` — 9 screens, served at `/studio` | — |

**60 API paths total** (39 from M9.5 + 21 catalog), no collisions.

---

## 3. Catalog Architecture

```
HTTP  →  command/query  →  Application  →  Foundation  →  Persistence
         catalog_admin      catalog.py     catalog_rules   catalog_repositories
```

Unchanged from M9.5 in every respect. The Application coordinates; **every catalog rule is
Foundation behaviour it calls** — code validity, duplicate detection, bundle-cycle detection,
edition conflicts, lifecycle legality, snapshot assembly, and search filtering. A test greps
the use cases for the rules that would be tempting to inline (`def validate_`, `find_cycle`,
`CATALOG_LIFECYCLE`) and finds none.

**Additive by construction.** `BillingRepositories` gained two defaulted fields (`features`,
`catalog`); `BillingContext.catalog` defaults to `None`; `BillingApplicationFactory` registers
catalog handlers onto the *same* buses only when those repositories are present. No existing
field changed type, no existing constructor call broke, and no layer moved.

Two frozen vocabulary modules were extended **additively**: `BillingEventType` gained 13
`billing.*` catalog facts and `BillingDiagnosticCode` gained 13 catalog codes. No existing
member was altered or removed (§A12).

---

## 4. Versioning Architecture

**The catalog is immutable after publication.**

* Publishing assembles a `CatalogVersion` — a **self-contained** snapshot embedding the
  products, editions, bundles, groups, plans, features, and meters as they were. Self-contained
  is the point: reading v7 never depends on what v12 did.
* Subscriptions reference `(org_id, version)` — a snapshot, never a mutable code. That is what
  makes a two-year-old invoice explainable.
* Versions are **per-organization** and monotonic; `next_version` never reuses a number.
* Historical versions remain readable forever. `cp_billing_catalog_version` is append-only, its
  repository has no update or delete method, and its `(org_id, version)` unique constraint makes
  republishing a database-level error. `SqlCatalogVersionRepository.publish` also raises
  `ImmutabilityViolation` above the constraint — defence in depth for the one property the
  domain cannot recover from losing.
* **Rollback publishes a new version** whose content is copied from an older one, recording
  `rolled_back_from` as provenance. The versions in between remain readable and unchanged. A
  rollback that tried to rewind the version number is rejected (`BillingConflict`).

Snapshot assembly is **pure and sorted**: two publishes of the same catalog are byte-identical,
which turns "did anything actually change between v7 and v8?" into a diff rather than an
investigation.

Alongside the snapshots, `cp_billing_catalog_activity` is an append-only history of *who
changed what, when* — written **inside the same transaction** as the change it describes. A
catalog whose history could disagree with its content would be worse than one with no history;
a test proves a rejected command leaves no activity behind.

---

## 5. Product Model

| Type | Purpose |
|---|---|
| `Product` | What a customer buys. Kind (platform/module/add-on/service), visibility, audience, feature and edition references, commercial metadata, `replaced_by_code` on deprecation. |
| `Edition` | A commercial edition of a product (Starter/Pro/Enterprise). `rank` orders upgrades **without encoding price** — an edition is above another *commercially*. |
| `Bundle` | Products (and, recursively, bundles) sold as one unit. Nesting allowed; cycles rejected. |
| `FeatureGroup` | A named set of features for presentation and reuse. Grants nothing. |
| `Feature`, `Plan`, `MeterDefinition` | Reused from M9.2 unchanged. |
| `CommercialMetadata` | Headline, description, documentation/marketing URLs, icon, tags, highlights — part of the snapshot, because what a customer was *told* they were buying is evidence. |
| `CatalogVersion` | The immutable snapshot. |
| `CatalogActivity` | One append-only history entry. |
| `CatalogSearchQuery` / `CatalogSearchResult` | A deterministic filter and its page. |

**Visibility is metadata, not permission.** It states what is publicly listed; Billing evaluates
no permission (ADR-0023 §5.3), and the consuming surface filters on it.

---

## 6. Lifecycle

One FSM for every catalog entity, on `core.fsm`:

```
draft ──publish──► published ──deprecate──► deprecated ──archive──► archived ──delete──► deleted
  │                    │                         │                      │
  └──────delete────────┘──────archive────────────┘        restore ◄─────┘
```

Three edges are deliberately absent, and each absence is a decision:

* **`published → deleted` does not exist.** A product a customer may hold must be deprecated and
  archived first — a one-step delete is exactly how a live product disappears by accident.
* **`deleted` is terminal.** A code that was once sold must never become silently reusable, and
  deletion is a *recorded state*, not a row removal.
* **`draft → deleted` does exist.** Nothing was ever sold under a draft.

`allowed(state)` returns the legal moves, and the API returns them on every product response —
so a UI renders buttons that cannot produce a 409, rather than buttons that fail. Illegal edges
raise `InvalidBillingTransition` → **409**, never a coerced adjacent state.

---

## 7. Compatibility Rules

The Foundation validator returns diagnostics and **never raises**, reports every finding, and
orders them deterministically — a publish wizard shows a complete checklist, not a whack-a-mole
sequence. It detects:

duplicate product / plan-version / feature / edition / bundle / meter codes · **bundle cycles,
reported as the offending path** (`a → b → c → a`, because "there is a cycle" is unactionable) ·
edition conflicts (two editions of one product at the same rank) · dangling references to
products, editions, plans, features, and meters · empty catalogs · invalid code characters ·
snapshot integrity · draft entities being snapshotted (warning, not a block).

Two rules worth naming:

* **Edition codes are unique within a product, not globally.** Two products may both have a
  "pro" edition; global uniqueness would be a naming tax with no payoff.
* **`known_products=None` means "not checked here"; an empty set means "this catalog has no
  products"** — and must reject the reference rather than skip the check. An empty-collection
  falsiness test loses that distinction silently, and a test now pins it.

---

## 8. Persistence

Six additive `cp_billing_*` tables via **migration 0016**, continuing the chain (0013–0015 →
0016). Same projection-plus-payload row shape, same codec, same org-scoped reads, same
"repositories never commit" rule. No existing table altered, no `ALTER TABLE` anywhere, no Epic
5–8 table touched — all asserted.

`cp_billing_catalog_version` and `cp_billing_catalog_activity` are append-only: their
repositories expose no update, replace, or delete method at all.

---

## 9. API

21 endpoints under `/v1/billing/catalog`: products (create, list, get, publish, deprecate,
archive, restore, duplicate), editions, bundles, features, feature groups, plans (get, publish,
archive, create-under-edition), search, versions (publish, rollback, history, latest, by
number), and activity.

**No path collides with M9.5.** `POST/GET /catalog/plans` already existed and is reused
unchanged — adding a second way to define a plan would have given the surface two ways to do
one thing. What M9.6 adds is what M9.5 could not express, including a **standalone plan-publish
endpoint**: M9.5 had to fold publishing into creation because no query could re-read a plan, and
`GetCatalogPlan` now can. **That closes a documented M9.5 limitation.**

Controllers stay transport-only, asserted on the AST: every route body reaches a bus, and none
contains a repository, a transaction, a validation call, or cycle detection. Product responses
are built by re-reading through `GetProduct` rather than from the command's aggregate,
specifically so `allowed_triggers` is computed by the Foundation instead of by importing the
state machine into a controller.

---

## 10. Studio

`/studio` serves a self-contained Catalog UI: **Products, Editions, Plans, Bundles, Features,
Versions, Publish (wizard), History, Search** — nine screens, one file, no build step, no CDN,
no external font.

ADR-0021 is respected literally: the Studio is a **Consumer**. It owns no state (no
localStorage, no sessionStorage), holds no rule, and knows only what the API tells it — every
screen renders a response and every action dispatches to `/v1/billing/catalog/*`. The publish
wizard's three steps are computed from API responses, and the lifecycle buttons it renders are
exactly the `allowed_triggers` the API returned.

**There is no pricing screen**, asserted against the tab list rather than the word "price" —
the UI does say *"what they pay is a price book"*, which explains the boundary rather than
crossing it.

---

## 11. Testing

**99 new tests**, in nine groups: lifecycle FSM (legal and illegal edges, terminality,
non-mutation) · validation (every duplicate class, cycles with paths, dangling references,
determinism, never-raises) · snapshots and rollback (self-containment, determinism,
immutability, provenance, no rewind) · search (determinism, composition, paging, all entity
kinds) · the Application over a real database (full lifecycle, duplication into a draft, edition
linking, bundle expansion, versioning, activity) · isolation, authorization, and durability
across a restart · migrations · the HTTP surface · Studio · and architecture compliance.

**Repository-wide: 2188/2188 pass** (2089 prior + 99, zero regression). `ruff check` and
`ruff format --check` clean.

Eight earlier boundary assertions were **sharpened rather than relaxed** — see §12.

---

## 12. Architecture Compliance

| Rule | Status |
|---|---|
| Catalog owns no money, price, currency, or rate | ✅ AST-asserted across 8 modules |
| Catalog owns no subscription, invoice, entitlement, usage, ledger, or payment | ✅ AST-asserted |
| Foundation stays pure (no SQL, HTTP, Pydantic, session) | ✅ |
| Layer direction unchanged; no layer moved | ✅ |
| Application contains no catalog rule | ✅ source-asserted |
| Controllers transport-only; every route dispatches through a bus | ✅ AST-asserted |
| Append-only stores expose no mutation path | ✅ |
| Migration chain extended (0016), idempotent, alters nothing | ✅ |
| Cross-org isolation, authorization on every command and query | ✅ |
| Frozen Epics 5–8 untouched; Billing absent from `bootstrap.py` | ✅ |
| Repository-wide suite green | ✅ 2188/2188 |

### Assertions sharpened, not weakened

Four earlier tests searched for a *word* that the catalog legitimately uses:

* **`rollback`** — `CatalogAssembler.rollback` publishes a new catalog version. The Foundation
  and controller "no transactions" checks now search for transaction *machinery*
  (`session.`, `.commit()`, `uow.`) rather than a word a domain may own.
* **`metric.`** in Studio/schema examples documents a *consumed* event, which is the opposite of
  the IR9 violation; the check now targets the layers that can emit, plus a new assertion that
  the transport layer has no emitter at all.

Four others asserted frozen counts (`len(__all__) == 203`, exact migration lists, exact table
sets). Those now assert **supersets**, which is the actual rule (§A12: surfaces grow additively
and nothing is ever removed) rather than a snapshot of one day's total. Two new assertions were
added. A rule that cannot distinguish a domain word from a database word would either block the
domain or get deleted; making it precise is the third option.

---

## 13. Known Limitations (stated, not hidden)

1. **Catalog snapshots and the M9.2 `CatalogSnapshotRepository` coexist.** M9.2 shipped a
   lightweight `CommercialCatalog` snapshot for rating; M9.6 adds the full `CatalogVersion`. Both
   are live and neither is wrong — the former is what pricing resolves against, the latter is what
   the commercial catalog publishes. Unifying them is an M9.7 decision, once pricing exists to say
   which it needs.
2. **`CreateCatalogPlan` sits beside M9.3's `DefinePlan`.** Two commands, deliberately two names:
   the M9.3 one is already dispatched by the M9.5 surface, and the catalog one additionally
   records activity and links an edition. One name that quietly changed meaning would be worse.
3. **Editions and bundles have no lifecycle endpoints yet** — the FSM and services support them
   (`apply_edition`, `apply_bundle`); only products expose transitions over HTTP. Additive.
4. **Search is in-memory over the org's catalog.** Deterministic and correct at catalog scale
   (tens to hundreds of entities); a projection or indexed query is the upgrade path if a tenant's
   catalog ever reaches thousands.
5. **The Studio has no auth flow** — a token is pasted in. It consumes the platform's identity;
   issuing one is not Billing's job (ADR-0023 §5.3).

---

## 14. Next

**M9.7 Pricing.** The catalog now says what is sold; pricing says what it costs, on its own
surface, versioned separately, resolving against a published catalog version. The boundary this
milestone drew — and asserted — is exactly the seam M9.7 attaches to.
