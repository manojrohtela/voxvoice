# ADR-0046 — Media-path fault tolerance: reusing the Provider Runtime's mechanisms without adopting its execution runtime, and discharging AR-24 / AR-25

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint** and settled at the **M11.0a** decision gate (rulings **D-7**, **D-10**, **D-13**); this milestone ratifies it, **including the dependency validation M11.0a required as a precondition** (§ Dependency validation below). |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. Supersedes no ADR. **AR-23 is satisfied, not scoped down.** |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A6.2** (every provider call passes through the fault-tolerance layer), **§A6.4** (declared degraded modes), **§A10** (fault tolerance and the degradation ladder), **§A0** (single name per concept), **§A2.6** (accepted config must act); ARCHITECTURE_RULES **AR-11**, **AR-15**, **AR-21**, **AR-23**, **AR-24**, **AR-25**, **AR-53**; GOVERNANCE §4 (*silent acceptance is a release blocker*), §10; [ADR-0001](ADR-0001-first-party-runtime.md) (interruption is owned differentiation); [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md); [ADR-0045](ADR-0045-streaming-adapter-pattern-and-vad-execution.md) (typed mid-stream failure); [P-ADR-0001](P-ADR-0001-implementation-profile-v1.md) (target-language streaming deferred to live verification) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code.** It fixes
the fault-tolerance semantics M11.4 must implement and every later milestone must honour.

## Context

**AR-23 is currently unsatisfied on the voice path, and this is Epic 11's largest inherited
debt.** The rule reads: *"Every provider call passes through the fault-tolerance layer — timeout,
typed error, circuit breaker, optional failover chain. **Direct raw calls are defects.**"*

Verified at `3779e932`: `RuntimePipeline` calls `self._stt.transcribe(...)`,
`self._llm.stream(...)` and `self._tts.synthesize(...)` **directly**. There is no timeout, no
typed translation, no breaker and no failover anywhere on the media path. With the text-loopback
providers this is harmless; with real vendors every call becomes an AR-23 defect.

Meanwhile `platform/provider/` (Epic 5, M5.6) already contains the machinery — `CircuitBreaker`
(*"injected clock, no timers"*), `FailoverController` (*"deterministic candidate-chain walk — no
retry loops"*), `ProviderHealthManager`, `ProviderPolicyEngine`, `ProviderRouter`,
`ProviderTenancy`. Three verified facts complicate simply using it:

1. It is **not on the voice path**: `grep -rn "^from provider\|from provider\."
   platform/runtime platform/providers platform/core` returns **zero** matches.
2. Its vendor seam is **synchronous**: `provider/ports.py::AdapterPort.stream(request) ->
   Iterator[ProviderChunk]`, with async explicitly deferred to a future `AsyncAdapter` extension
   hook (`provider/extensions.py:37`, docstring: *"Future"*).
3. It is declared **architecturally closed**: *"The Provider Runtime is architecturally closed
   after this milestone except additive extensions + bug fixes"* (`provider/__init__.py`).

Its execution model is also request/response-shaped (`ProviderRequest → Iterator[ProviderChunk]`),
built for LLM-style calls — not for a duplex media session that lives for minutes.

Separately, **four** classes are named `ProviderRegistry`: `runtime/registry.py:150`,
`provider/registry.py:22`, `reasoning/engine.py:30`, `billing/foundation/payment_rules.py:369` —
against §A0's *"single name per concept."*

Two further verified silent-config defects fall in this ADR's scope:

- **`allow_interruptions` never reaches the runtime.** `AgentSpec.allow_interrupt` defaults
  `False`; `core/session_control.py::StartSessionRequest` has **no such field**; and
  `RuntimeSessionService.start()` builds `AgentSpec` without it. The API exposes
  `allow_interruptions` defaulting `True` (`api/schemas.py:32`). **Barge-in is therefore
  unreachable in production**, though the Interrupt framework (M3.4/M3.5) is complete and tested.
- **`llm_model` is accepted and never read.** `ConversationOrchestrator` resolves
  `registry.resolve(CapabilityKind.LLM)` with no selector, while `stt_provider` and
  `tts_provider` are honoured via `_sel(...)`.

## Dependency validation (precondition required by M11.0a)

M11.0a made ADR acceptance conditional on independently validating the proposed
`runtime → provider` edge. The validation was performed at M11.1 against the repository and
returned a **split result**.

### FT-V1 — `provider.CircuitBreaker` is cleanly reusable

`platform/provider/circuit_breaker.py` imports **only the standard library**:

```
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
```

Its public API is name-keyed and clock-injected — `allow(name, *, now)`, `record_success(name)`,
`record_failure(name, *, now)`, `state_for(name)`, `statistics` — and mentions no
`ProviderRequest`, no domain type and no sibling module. A `runtime → provider.circuit_breaker`
edge imports **zero** additional modules.

### FT-V2 — `provider.FailoverController` is **not** cleanly importable as it stands

`platform/provider/failover.py` imports `from .router import RoutingDecision`, and
`platform/provider/router.py` in turn imports `core.errors`, `.catalog`, `.domain`, `.ids`,
`.policies` and **`.registry`**. Importing `FailoverController` today would therefore transitively
bind the media path to the Provider Runtime's request/response domain model **and to the second
`ProviderRegistry`** — exactly what FT-D2 below prohibits.

The coupling is nonetheless **shallow**: `RoutingDecision` appears only as a parameter
annotation, and the method body reads exactly one attribute, `decision.candidates`.

### FT-V3 — No cycle exists or is created

`grep -rn "^from runtime\|^import runtime\|from runtime\." platform/provider/` returns **zero**
matches. `provider/` imports only `core` (and `tenant`). `runtime/` today imports `core` and
`events`. A `runtime → provider` edge is therefore **acyclic**, and **AR-15** remains satisfied.

### FT-V4 — AR-11 is untouched

AR-11 constrains `providers/` (the capability plugins). Nothing in this ADR causes `providers/`
to import anything but `core`. The edge under discussion is `runtime → provider`, a different
package.

### FT-V5 — No reverse dependency is introduced

Nothing in this ADR causes `provider/` to import `runtime/`. FT-D3's widening removes an import
from `provider/`; it adds none.

### FT-V6 — No fifth `ProviderRegistry` is required

The media-path layer resolves capabilities through the existing
`runtime/registry.py::ProviderRegistry`. It introduces no registry of its own.

## Decision

### FT-D1 — AR-23 is enforced in full on the media path

Every provider call on the media path passes through a fault-tolerance layer providing
**timeout + typed error + circuit breaker + ordered failover**. AR-23 is **not** scoped down,
narrowed or excepted by this ADR.

### FT-D2 — The voice runtime is **not** routed through `platform/provider/`'s execution runtime

`AdapterPort` is synchronous and request/response-shaped; a duplex media session lasting minutes
is the wrong shape for it, and forcing it would reopen a subsystem declared closed. The Provider
Runtime keeps its own scope unchanged.

### FT-D3 — Mechanisms are **reused**, not rebuilt

- **`CircuitBreaker` is reused by direct import** (validated: FT-V1, FT-V3).
- **`FailoverController` is reused by widening its seam, not by copying its code.** Per the
  M11.0a fallback (*lift the reusable mechanism to an architecturally valid shared location; do
  not rebuild*), M11.4 introduces a minimal **structural `Protocol`** describing an ordered
  candidate chain on the `core` floor — an interface, therefore legal there under
  `core/__init__.py`'s no-behaviour rule — and widens `FailoverController.next`'s annotation to
  it. `RoutingDecision` satisfies that Protocol **structurally and already**, so the change is
  additive and behaviour-preserving, and it **deletes** `failover.py`'s `.router` import,
  collapsing the transitive closure identified in FT-V2 to nothing.

  This is an additive extension to a subsystem whose closure statement permits exactly that.
  **Neither mechanism is reimplemented anywhere.**

### FT-D4 — The layer is a thin decorator in `runtime/`, and it owns no policy of its own

The media-path layer wraps **capability-contract calls**. It contains no vendor branching
(AR-21), constructs no provider, and adds no registry (FT-V6).

### FT-D5 — Ordered failover is per capability, and chains are configuration

Failover walks an ordered candidate chain per capability (§A6.2). It is **selection only** — no
retry loops, no backoff, no sleeping — preserving `FailoverController`'s existing semantics.

### FT-D6 — The full §A10 degradation ladder is the required behaviour

**failover → declared degraded mode → inform-the-caller policy → clean session failure with a
full event trail.** Exhaustion is a clean failure, never a hang.

### FT-D7 — AR-24 degradation recording becomes real

Degradations are **declared, evented and recorded in session metadata**. Today
`core/providers/stt.py`'s docstring claims *"the runtime adapts and records the degradation
(AR-24)"* and **no code does so**. M11.4 makes the claim true.

### FT-D8 — AR-24 must land **before** the target-language provider

Because a degradation that cannot be recorded is a silent degradation, **M11.4 is hard-before
M11.6/M11.10**. This is the mechanism that lets [P-ADR-0001](P-ADR-0001-implementation-profile-v1.md)'s
deferred target-language verification produce an honest answer: Sarvam ships with whatever
`streaming` flag the M11.3 conformance suite proves, and if that is `False` the runtime adapts
and records it. **Epic 11 does not pre-judge the result** (D-13).

### FT-D9 — AR-25: accepted configuration must act, or the write is rejected

Three fields are discharged at M11.4:

| Field | Disposition |
|---|---|
| `allow_interruptions` | **Implemented.** Added to `StartSessionRequest`, mapped to `AgentSpec.allow_interrupt`. VAD defaults **on** in the real-provider profile; the local profile keeps its current default so the 5447 floor holds. |
| `llm_model` | **Implemented.** Honoured through the same `_sel(...)` selector path already used for `stt_provider` and `tts_provider`. |
| `first_message` | Implemented at M11.10 — see [ADR-0048](ADR-0048-llm-tts-clause-pipelining-and-playback.md). |

**Barge-in is fixed in Epic 11, not deferred as a pre-existing defect.** GOVERNANCE §4 makes
silent acceptance a **release blocker** and §10 blocks release on open Severity-High findings;
age grants no exemption. Independently, ADR-0001 names *interruption* as owned differentiation —
a live runtime that cannot be interrupted is not the product.

### FT-D10 — The `ProviderRegistry` name collision is accepted debt, not repaired here

Four classes share the name. Renaming any of them touches frozen Epic 5/9 surfaces for zero
Epic 11 capability. Recorded as **accepted debt with a repayment note** under **AR-53** and in the
DECISION_LOG; a rename is a separate refactor outside Epic 11.

## Consequences

- M11.4 implements FT-D1–FT-D9 and is **hard-before** M11.5 and M11.6.
- One new package edge exists: `runtime → provider` (`circuit_breaker`, `failover`). Validated
  acyclic (FT-V3).
- `platform/provider/failover.py` receives one additive, behaviour-preserving annotation
  widening at M11.4 and no other change. `provider/circuit_breaker.py` is not modified at all.
- `core` gains one `Protocol` definition and no behaviour.
- A standing AR-23 defect and two standing AR-25 defects are discharged.
- Barge-in becomes reachable for the first time.

## Alternatives considered

- **Route the voice path through `platform/provider/`.** Rejected: FT-D2 — synchronous,
  request/response-shaped, declared closed.
- **Rebuild a breaker and a failover controller in `runtime/`.** Rejected: duplicate
  infrastructure. `provider.CircuitBreaker` is already deterministic with an injected clock and no
  timers — the exact shape the media path needs.
- **Import `FailoverController` as-is.** Rejected on evidence: FT-V2's transitive closure would
  drag the Provider Runtime's domain model and its `ProviderRegistry` onto the media path.
- **Copy `FailoverController`'s twenty lines into `runtime/`.** Rejected: it is a rebuild in all
  but name, and M11.0a's fallback says lift, not rebuild.
- **Scope AR-23 by ADR to exclude the media path.** Rejected outright. An ADR whose wording makes
  a violation "allowed" is prohibited by the M11.1 charter, and the media path is precisely where
  vendor failure is most likely and most visible to the caller.
- **Treat barge-in as a pre-existing defect for a later epic.** Rejected: FT-D9.
- **Rename the colliding registries now.** Rejected: FT-D10.

## Evidence required later

1. A test proving **every** media-path capability call carries a timeout.
2. Breaker-opens and ordered-failover-walk tests over the media path.
3. An acyclicity / import-boundary test asserting the `runtime → provider` edge and the absence
   of any `provider → runtime` edge.
4. A test proving no fifth `ProviderRegistry` exists.
5. A degraded-mode test: a declared degradation is evented and recorded in session metadata.
6. An end-to-end **barge-in** test through the real-provider profile.
7. AR-25 tests for `allow_interruptions` and `llm_model`.
8. Proof that `provider/failover.py`'s widening changed no behaviour — the existing Provider
   Runtime suite green, unmodified.
