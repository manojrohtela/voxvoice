# Memory Runtime (Epic 5, M5.1)

| | |
|---|---|
| **Scope** | The provider-neutral, deterministic, event-driven runtime that **only manages memory** · **Status:** Core implemented (M5.1B-1) |
| **Milestone** | M5.1A — Design (approved) · M5.1B-1 — Core (2026-07-18, additive) |
| **Basis** | The approved M5.1A Memory Runtime Design; the M5.0 [AI Runtime Foundation](ai-runtime-foundation.md) |
| **Governs** | `platform/memory/` |

## Purpose
A single bus-attached module that stores, retrieves, and forgets memory records —
and does **nothing else**. It never reasons, never renders prompts, never executes
tools. It is a deterministic projection of the event stream into a tenant-scoped,
provider-neutral store, built entirely on the M5.0 substrate: a `BusConsumer` /
`RuntimeModule` implementing `core.ports.MemoryPort`, a `core.fsm` record lifecycle, a
repository layer over the `core.providers.stores` contracts, and `memory.*` events
that Replay records and the Gateway streams **for free**.

## What landed in M5.1B-1 (core only — all additive)
Nothing outside `platform/memory/` (+ one new test file) changed; runtime / events /
gateway / replay / core are untouched. 288 prior tests unchanged; 314 total.

### 1. Domain model (`memory/domain.py`)
`MemoryRecord` (immutable aggregate) + `MemoryId`, `MemoryType`, `MemoryState`,
`MemoryMetadata`, `MemoryFilter`, `MemoryResult`. `MemoryTier`, `MemoryScope`,
`MemoryQuery`, `MemoryHit` are **reused from `core.ports.memory`** — never redefined.
All frozen + slotted (deterministic, replay-friendly).

### 2. Record lifecycle (`memory/lifecycle.py`) — dogfoods `core.fsm`
`CREATED → STORED → ACTIVE → ARCHIVED → EXPIRED → DELETED` (plus `ACTIVE/ARCHIVED
→ DELETED` for explicit forget). `advance(record, trigger)` validates one transition
on an immutable record and returns it in its new state; an illegal move raises a typed
`FSMError`. No hand-written transition logic (per M5.0).

### 3. Repository layer (`memory/repositories/`) — the anti-corruption boundary
`MemoryRepository` (records over `DocumentStore`), `VectorRepository` (write-path
embed+upsert over `VectorStore`+`Embedder`), `MetadataRepository` (cursors/counters
over `MetadataStore`). **Why it exists:** the runtime speaks only `MemoryRecord` /
`MemoryFilter`; the repos are the *only* code binding the storage contracts and
translating domain ↔ storage. This keeps the runtime provider-neutral, database-neutral,
and testable with in-memory stores. Repositories carry **no ranking and no policy** —
pure translation + attribute filtering.

### 4. Local storage adapters (`memory/stores/`, development only)
Deterministic dict-backed `InMemoryDocumentStore` / `InMemoryVectorStore` /
`InMemoryMetadataStore` + a `DeterministicLocalEmbedder` (SHA-256 hashing projection —
**not** Python's per-process-salted `hash()`; pure, normalized, offline). They make the
runtime fully runnable/testable with zero infrastructure. A production adapter
(Postgres / pgvector / Qdrant / Redis) swaps in behind the same contracts via the
Provider Registry with no runtime change.

### 5. Serializer (`memory/serializer.py`)
One deterministic translation `MemoryRecord ↔` the four dict shapes: **storage**
(round-trippable), **events** (privacy-lean — ids + lengths, never content on the bus),
**API** (a stable public view), **hits** (`MemoryHit`). The embedding vector is not
serialized (it lives only in the vector store; the record records `embedded`).

### 6. Configuration + factory (`memory/config.py`, `memory/factory.py`)
`MemoryConfiguration` (frozen, validated) declares store kinds, embedder, per-tier
options, cache settings. `MemoryFactory` is the composition root — the only place
adapter kinds are named; an unknown kind fails fast with `ConfigError`. TTL / capacity
/ cache fields are **data only** (their logic is a later milestone).

### 7. `MemoryRuntime` (`memory/runtime.py`)
Implements the sync `MemoryPort` over an async core (`astore` / `aretrieve` /
`aforget`), publishes `MEMORY_STORED` / `MEMORY_RETRIEVED` / `MEMORY_CLEARED`, and works
with or without a bus (the bus is for observability). Semantic-tier records are
vectorized on store.

## Two reconciliations (recorded in DL-0021)
- **Sync ↔ async.** `MemoryPort` and the in-process bus are synchronous, but the store
  contracts are `async`. `memory._run.run_coro_sync` drives a *non-suspending*
  coroutine (the local adapters never `await` a Future) to completion with **no event
  loop** — deterministic. A network-backed store must use the async API or the driver
  fails loudly (never silently blocks).
- **`retrieve` placeholder.** The port's `retrieve` is abstract, so it must be concrete
  now — but the Retrieval Engine is a non-goal. It is a documented **naive attribute
  filter** over the repository (scope / tier / tags, score = importance, gated by
  `min_score`; no ranking, semantic, or keyword search), replaced in M5.1B-2. The
  in-memory `VectorStore.search` is implemented (the ABC requires it) but **unused** by
  the runtime — semantic search stays deferred.

## Dependency graph (acyclic, verified by test)
```
core (events · errors · fsm · module · ports · providers/stores)
  ▲                         ▲
  │                         │  (bus impl injected, never imported)
memory/  ── domain · lifecycle · serializer · config · repositories · stores · runtime · factory
```
`memory/` imports only `core` (+ its own package); never a sibling runtime, the bus
implementation (`events/`), `api/`, `gateway/`, or `replay/`. A test enforces this
(relative imports are excluded so `memory/runtime.py` doesn't shadow the top-level
`runtime` package).

## Non-goals (later milestones)
Retrieval Engine · ranking · policies (capacity / expiration / retention / merge /
conflict / privacy) · expiration / eviction · semantic / keyword / hybrid search ·
Memory Cache · content-event ingestion (the runtime attaches to publish only) ·
Context / Knowledge / Reasoning / Workflow / Agent runtimes. All additive behind the
same contracts.
