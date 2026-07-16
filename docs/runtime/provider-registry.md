# Runtime Component Spec — Provider Registry

| | |
|---|---|
| **Component** | `runtime.registry.ProviderRegistry` · **Status:** Implemented (M2C.1) |
| **Format** | Constitution §A11.3 component specification |
| **Milestone** | M2C.1 — Provider Registry (2026-07-16) |

## Responsibility (single)
Manage **provider *references* by capability** only: register, unregister,
resolve, and list capability-provider instances, validating that each satisfies
its capability contract. Nothing else — no provider construction, no execution,
no network calls, no runtime resource allocation, no vendor-specific behaviour.
The Runtime receives providers via dependency injection; the registry never
creates one.

## Placement (DL-0008)
Lives in `runtime/registry.py` — a flat module, not a subpackage (to avoid a
naming collision with `core/providers/` [interfaces] and the top-level
`providers/` [implementations]). Constitution §A4.1's layer rule literally says:
*"`runtime/` imports `core/` and provider interfaces (**resolved via
registry**)"* — the registry is how the Runtime Plane resolves provider
interfaces, so it is a Runtime Plane component. `core` remains behaviour-free
(contracts/definitions only, per `core/__init__.py`); `providers/` remains
implementation-only.

## Capability model
The registry understands **capabilities**, never vendors:

| `CapabilityKind` | Contract (`core.providers`) |
|---|---|
| `stt` | `STTProvider` |
| `tts` | `TTSProvider` |
| `llm` | `LLMProvider` |
| `transport` | `TransportProvider` |
| `storage` | `StorageProvider` |
| `analytics` | `AnalyticsProvider` |

New capability kinds are a purely additive extension (Constitution §A12): add a
base-class mapping, nothing else changes. A provider's `name` (e.g. a vendor's
chosen identifier) is treated as an **opaque dictionary key** — the registry has
no `if name == "<vendor>"` branch anywhere; callers request a capability,
optionally by that opaque name, never by vendor identity.

## Registration & resolution
- `register(kind, provider, *, default=False)` — validates and stores a
  reference; returns `ProviderMetadata`. At most one `default` per capability.
- `unregister(kind, name)` — removes a reference; raises if absent (no silent
  no-op).
- `replace(kind, provider, *, default=False)` — atomic hot-swap: unregister any
  existing `(kind, name)` entry, then register the new instance. Supports future
  hot-swapping without ever executing anything.
- `resolve(kind, name=None)` — by name if given; else the default if set; else
  the sole candidate if exactly one is registered; else an explicit
  `CapabilityNotRegistered` (no silent/random pick — deterministic by
  construction).
- `has(kind, name=None)`, `list(kind=None)` — discovery; `list()` returns an
  immutable, registration-ordered tuple of `ProviderMetadata` (capability flags
  only — never the provider instance).

## Validation rules (typed errors, all under `runtime.errors.ProviderRegistryError`)
| Rule | Error |
|---|---|
| Same `(kind, name)` registered twice | `DuplicateProviderRegistration` |
| A default already set for the kind | `DuplicateProviderRegistration` |
| Provider fails `isinstance` against the kind's ABC | `ProviderCapabilityMismatch` |
| Provider is `None`, or missing `name`/`capabilities` | `InvalidProviderRegistration` |
| `kind` has no known base-class mapping | `UnsupportedCapabilityKind` |
| Resolve with no provider / unknown name / ambiguous (no default, multiple candidates) | `CapabilityNotRegistered` |

No silent fallback anywhere — every illegal call raises explicitly.

## Concurrency & determinism
Thread-safe via one `threading.RLock` guarding all reads/writes (reentrant so
`replace()` can call `unregister`+`register` under the same lock). Purely
synchronous — no `async`, since there is no I/O to await. Deterministic:
insertion-ordered storage (`dict`), explicit default/ambiguity rules, no
randomness — the same call sequence always yields the same state and
resolution.

## Observability
Registry lifecycle notifications only (`RegistryEvent`: `registered` /
`unregistered`) via an optional synchronous observer + structured logs. This is
**not** the Event Bus (M4); a bridge can forward these later. An observer that
raises is swallowed and logged — never breaks the registry.

## Dependencies & boundaries
Imports `core.providers` (capability contracts) and `runtime.errors`. Pure
stdlib otherwise (`threading`, `time`). Never imports `api`; never a provider
implementation.

## Not in scope (later milestones)
Provider construction/bootstrap wiring, `_open_providers`/`_close_providers`
execution in the Orchestrator, the turn loop (M2C.2+) · Event Bus emission (M4).
