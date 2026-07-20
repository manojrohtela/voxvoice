# Prompt Runtime (Epic 5, M5.4) — Design

| | |
|---|---|
| **Scope** | The provider-neutral, deterministic, event-driven runtime that **owns prompt construction only** · **Status:** 🟢 Core implemented (M5.4B-1); intelligence M5.4B-2 |
| **Milestone** | M5.4A — Design (approved) · M5.4B-1 — Core (2026-07-20, additive) · M5.4B-2 — Intelligence (planned) |
| **Basis** | The frozen M5.0 [AI Runtime Foundation](ai-runtime-foundation.md) (`core.ports.PromptPort`); the closed [Memory](memory-runtime.md) / [Knowledge](knowledge-runtime.md) / [Context](context-runtime.md) Runtime patterns |
| **Governs** | `platform/prompt/` |

> Sections 1–18 below are the **approved M5.4A design**. The **"What landed in M5.4B-1"**
> section (after §18) records the implemented core. The design redesigns **nothing**
> already closed (Foundation, Memory, Knowledge, Context, Ports, Repositories, Events);
> where a closed runtime seemed insufficient it is an *additive extension* or a *bug*.

---

## 1. Executive Summary
The **Prompt Runtime** turns a finished `AssembledContext` (plus templates + generation
parameters) into **one immutable, budgeted, validated, provider-neutral `PromptPackage`**
that the future Reasoning Runtime hands straight to a model **without ever reconstructing a
prompt or re-querying Context/Memory/Knowledge**. It owns **prompt construction and nothing
else**: it never assembles context, reasons, calls a model, runs tools, or manages
workflows/agents. It consumes every input **through a `core` port or an immutable object**
(dependency inversion), so it imports no sibling runtime and stays acyclic
(`prompt → core, tenant`). It implements the **frozen** `core.ports.PromptPort` and reuses
the closed-runtime shape: a `BusConsumer`/`RuntimeModule`, a repository layer over the store
contracts (**temporary** package storage only), `core.policies`-based deterministic
budget/validation, injected `ProviderProfile`/`ModelProfile`/`Tokenizer` ports for anything
vendor- or model-specific, and `prompt.*` standard envelopes Replay records and the Gateway
streams for free. After M5.4B it will be **architecturally closed** except additive
extensions and bug fixes.

## 2. Prompt Runtime Vision
One handoff, one contract, one package.

- **The clean input boundary.** Prompt consumes the **frozen `core.ports.context.AssembledContext`**
  — Context's already-narrowed, provenance-complete output — never the `context`
  `ContextPackage` (which would couple sibling runtimes). The Context→Prompt handoff is a
  **composition-root bridge** implementing Context's `ContextConsumer`, so neither runtime
  imports the other.
- **The "construct once" guarantee.** The `PromptPackage` carries the fully-rendered,
  provider-shaped messages + generation parameters + complete lineage, so **Reasoning never
  reconstructs a prompt** — it reads the package and calls the model.
- **Provider neutrality.** Rendering to Anthropic / OpenAI / Gemini / local formats is an
  injected `ProviderProfile` adapter; token budgeting to a model window is an injected
  `ModelProfile`/`Tokenizer`. A new provider is a new adapter — **never** a Prompt change.
- **Deterministic + replayable.** Templates are pure string substitution (no logic, no LLM);
  content-hash ids + injected clock make construction byte-for-byte reproducible; every
  stage emits a standard envelope.

## 3. Component Architecture
All new, inside the planned `platform/prompt/` package. Grouped **core** (M5.4B-1) vs
**intelligence** (M5.4B-2), mirroring Memory/Knowledge/Context.

```
prompt/
  domain.py          PromptSection*/PromptPackage/PromptMessage/PromptLineage (immutable)
  ids.py             content-hash ids (section id, prompt hash, template version)
  lifecycle.py       core.fsm package lifecycle: REQUESTED→BUILT→VALIDATED→RENDERED→
                     DELIVERED / REJECTED / EXPIRED
  ports.py           runtime-internal seams: ProviderProfile, ModelProfile, Tokenizer
  templates.py       Template + TemplateRegistry (temporary) + TemplateResolver
  sections.py        SectionGenerator — AssembledContext items + templates → PromptSections
  builder.py         PromptBuilder — compose sections → PromptPackage
  renderer.py        PromptRenderer — provider-neutral → provider-shaped messages (via port)
  formatter.py       PromptFormatter — deterministic section/message formatting
  repositories/      PromptRepository (TEMPORARY package + template store) — sole store-toucher
  stores/            in-memory Document/Metadata adapters + default Tokenizer (dev only)
  request.py         PromptRequest / GenerationParameters (immutable inputs)
  budget.py          PromptBudgetManager (window/reserve/completion/overflow/adaptive)   [I]
  validation.py      PromptValidationEngine (template/prompt/budget/placeholder/provider…) [I]
  policies.py        PromptPolicyEngine + policy value objects → verdicts                 [I]
  tenancy.py         PromptTenancy (org/private/shared templates; isolation-first)         [I]
  extensions.py      interfaces only: TemplateProvider/TemplateStore/PromptOptimizer/
                     MultiModalRenderer/PromptConsumer                                     [I]
  observability.py   PromptStatistics + Metric names + per-stage stats
  events.py          PromptPhase (phase discriminator on prompt.* envelopes)
  config.py          PromptConfiguration (budgets, provider, templates, collections)
  factory.py         PromptFactory (composition root; injects the profiles/ports)
  runtime.py         PromptRuntime(BusConsumer, PromptPort)
  _run.py            run_coro_sync (sync↔async bridge, local adapters only)
```
`[I]` = intelligence (M5.4B-2). The **runtime depends only on injected profiles/ports**
(`ProviderProfile`/`ModelProfile`/`Tokenizer`/`TemplateStore`) and the frozen
`AssembledContext` — never on `context`, `memory`, `knowledge`, or a provider SDK.

## 4. Ownership Matrix
| Concern | Owner | Not owned by |
|---|---|---|
| Prompt construction orchestration | **PromptRuntime / PromptBuilder** | — |
| Template resolution | **TemplateResolver / TemplateRegistry** | — |
| Section generation | **SectionGenerator** | — |
| Provider-shaped rendering | **PromptRenderer** + injected `ProviderProfile` | Prompt (no vendor SDK) |
| Token budgeting to the model window | **PromptBudgetManager** + injected `ModelProfile` | Prompt (no tokenizer vendor) |
| Validation gate | **PromptValidationEngine** | — |
| Temporary package/template storage | **PromptRepository** | Memory (long-term is Memory's) |
| Tenant isolation (template-time) | **PromptTenancy** | — |
| **Assembled context** | injected `AssembledContext` (**Context Runtime**) | Prompt (consumes only; never assembles) |
| **Generation parameters** | carried immutably; **interpreted by Reasoning** | Prompt (passes through, never runs inference) |
| Final immutable package | **PromptPackage** | downstream (read-only) |

**Prompt Runtime never owns:** Memory · Knowledge · Context Assembly · Reasoning · LLMs ·
Inference · Tools · Workflows · Agents. It *constructs the prompt and hands it off*.

## 5. Prompt Construction Pipeline
Async-first (the Memory M5.1C decision); the sync `PromptPort` drives it via the
non-blocking bridge (local adapters only). Every stage is deterministic and Prompt-owned.

| # | Stage | Owner | Behaviour |
|---|---|---|---|
| 0 | **Input** | PromptRuntime | Validate `PromptRequest` (+ the frozen `AssembledContext`, `ProviderProfile`, `ModelProfile`, `GenerationParameters`, metadata); **tenancy guard first**. |
| 1 | **Template Selection** | TemplateResolver | Resolve the template set by precedence (request override > user > org > shared > default), scoped to the tenant. |
| 2 | **Section Generation** | SectionGenerator | Map `AssembledContext` items (by kind) + resolved templates → typed `PromptSection`s (system/developer/user/conversation/knowledge/tool/workflow/environment), each carrying **lineage** (template + context source). |
| 3 | **Budget Allocation** | PromptBudgetManager | Fit sections into the model window − completion reserve − reserved (system/developer) via the injected `ModelProfile`/`Tokenizer`; overflow → truncate lowest-priority *context* sections (never instructions). |
| 4 | **Formatting** | PromptFormatter | Deterministic per-section formatting (whitespace, delimiters, citation markers) — provider-neutral. |
| 5 | **Validation** | PromptValidationEngine | Template/prompt/budget/placeholder/provider/safety/citation **verdicts**; fail → reject. |
| 6 | **Rendering** | PromptRenderer + `ProviderProfile` | Assemble provider-shaped **messages** (role mapping, system handling) — still neutral data, no network. |
| 7 | **Prompt Package** | PromptBuilder | Build the immutable `PromptPackage` (sections + messages + params + provider/model refs + budget/validation/lineage + hash + version). |
| 8 | **Delivery** | PromptRuntime | Persist to the **temporary** repository; emit `prompt.*`; hand to Reasoning via the `PromptConsumer` seam (composition root). |

A final **validation gate** (post-render) guarantees *no prompt leaves unvalidated* →
`PromptRejected` on failure.

## 6. Prompt Package Design
`PromptPackage` (frozen, slotted, deterministic) — the construct-once artifact.

- **Sections** (each a `PromptSection`, by role/kind): `system`, `developer`, `user`,
  `conversation_context`, `knowledge_context`, `tool_context`, `workflow_context`,
  `environment_context`.
- **Per section:** `id` (content-hash), `role`, `kind`, `content`, **`lineage`**
  (`PromptLineage`: template source id+version, context source ids + provenance, rendering
  version, section version, transformation metadata, hash), `token_estimate`.
- **Rendered view:** `messages` — the provider-neutral ordered `PromptMessage`s
  (`role` + `content`) the `ProviderProfile` produced (the exact shape Reasoning sends).
- **Package-level:** `id`, `prompt_hash`, `prompt_version`, `generation_parameters`
  (carried, not interpreted), `provider_profile` ref, `model_profile` ref,
  `provider_metadata`, `rendering_metadata`, `budget_usage`, `validation_metadata`,
  `scope`, `created_at`, `expires_at`.
- **Interop:** `to_fragment()` narrows to the **frozen** `core.ports.PromptFragment` (the
  `PromptPort` contract) for a single-template render path; the rich package is the additive
  surface. **Self-sufficient** — Reasoning reads `messages` + `generation_parameters` and
  calls the model; it never reconstructs a prompt or re-queries Context/Memory/Knowledge.

The frozen `PromptFragment` (template_id/version/text/metadata) is honoured, not edited; the
richer package maps down to it.

## 7. Template Architecture
- **`TemplateRegistry`** — the in-memory temporary registry + an injected **`TemplateStore`**
  port for durable org/user templates (implemented later; the seam is neutral now).
- **`Template`** — id, version (content-hash + semver), `role`/`kind`, body (pure
  placeholder string, e.g. `{{context.knowledge}}`), scope (org/user/shared), placeholders.
- **`TemplateResolver`** — deterministic precedence: **request override > user > org >
  shared > built-in default**, scoped to the tenant *after* the isolation guard.
- **Versioning** — every template is immutable + content-hashed; a change registers a new
  version (emits `prompt.template.updated`); packages pin the exact versions used (lineage).
- **Composition** — templates may include sub-templates (`{{include:header}}`), resolved
  deterministically (acyclic include-graph; cycles rejected at registration).
- **Overrides** — a request may supply inline template overrides (highest precedence).
- **Dynamic templates** — a future injected **`TemplateProvider`** port (e.g. a template
  synthesized elsewhere) — additive, no resolver change.

Prompt Runtime owns template resolution end-to-end; it never fetches templates from Memory
or Knowledge.

## 8. Rendering Architecture
Provider-neutral core → provider-shaped data via the injected **`ProviderProfile`** port:
- The renderer produces role-tagged sections; the `ProviderProfile` maps them to the
  provider's message convention (**Anthropic** system + messages; **OpenAI** messages array;
  **Gemini** contents/parts; **local** chat templates) — returning neutral `PromptMessage`
  data, **never** a network call or SDK object.
- Adapters implement `ProviderProfile`/`ModelProfile`; **a new provider is a new adapter and
  changes nothing in the Prompt Runtime architecture** (the renderer/builder/pipeline are
  provider-agnostic).
- Deterministic: pure substitution + stable ordering; the injected clock supplies any
  timestamps. Multi-modal rendering is a future `MultiModalRenderer` port (text-only now).

## 9. Budget Architecture
`PromptBudgetManager` owns **prompt allocation only** (Context already budgeted the context;
Prompt re-budgets to the *model's* window) on `core.policies.BudgetPolicy` where it fits:
- **Token allocation** — per-section estimates via the injected `ModelProfile`/`Tokenizer`.
- **Reserved tokens** — system + developer sections always kept.
- **Completion reserve** — `model_window − reserve` is the prompt ceiling (guarantees room
  for the model's output).
- **Overflow strategy** — truncate/drop the **lowest-priority context sections** first
  (knowledge/tool/workflow/conversation), never instructions; `reject` when even reserved +
  user won't fit.
- **Prompt truncation** — deterministic trimming of context sections (with a marker;
  lineage records what was trimmed).
- **Adaptive prompt budget** — redistribute unused reserve to context sections
  deterministically.
- **Future model-aware budgeting** — the `ModelProfile` supplies the exact window + counter;
  default is a deterministic estimator. Additive — no logic change.

## 10. Validation Architecture
`PromptValidationEngine` — **verdicts only**; the runtime performs side effects (reject).
- **Template validation** — every referenced template resolved + versioned.
- **Prompt validation** — well-formed messages, valid roles, non-empty required sections.
- **Budget validation** — final prompt within window − completion reserve.
- **Placeholder validation** — no unresolved `{{…}}` remain.
- **Provider validation** — the rendered shape is valid for the target `ProviderProfile`.
- **Safety validation** — a `SafetyValidator` **hook** (interface only; no classifier here).
- **Citation validation** — knowledge/tool sections carry the citations from the context
  provenance (compliance).

## 11. Policy Architecture
`PromptPolicyEngine` turns immutable policy value objects into **verdicts** (no I/O, no side
effects), reusing `core.policies` where one fits: **Template · Rendering · Budget ·
Validation · Provider · Citation · Safety**. Bundled as `PromptPolicies`; validated at
construction (fail fast).

## 12. Event Flow
Prompt is the **sole producer** of `prompt.*` events, publishing standard envelopes
(Replay/Gateway free). **No Event Bus change, no new EventType**: the seven logical events
map onto the **existing** M5.0 `PROMPT_RENDERED` (+ a `phase` discriminator) and template
changes onto `PROMPT_TEMPLATE_UPDATED` — the exact Memory/Knowledge/Context pattern.
Granular per-phase `EventType`s are a deferred additive vocabulary extension.

| Logical | Emitted as | phase |
|---|---|---|
| PromptRequested / Built / Validated / Rendered / Rejected / Delivered | `PROMPT_RENDERED` | `requested` / `built` / `validated` / `rendered` / `rejected` / `delivered` |
| PromptExpired | `PROMPT_RENDERED` | `expired` |
| (template registry change) | `PROMPT_TEMPLATE_UPDATED` | — |

Prompt is **request-driven (pull)** — it does not ingest content events; its default bus
subscription is empty and it never ingests a `prompt.*` event (structural self-loop
prevention, like the closed runtimes).

## 13. Tenancy
`PromptTenancy.guard(scope, tenant)` runs **before template resolution** (isolation-first),
wiring the M5.0 `tenant` hooks (`Authorizer`/`QuotaEnforcer`/`AuditSink`) with permissive
no-op defaults — the same seam Memory/Knowledge/Context use (`prompt → tenant` is a leaf
foundation dependency; acyclic). Levels: **organization templates** (org-visible),
**private templates** (owner-scoped), **shared templates** (explicitly granted), **tenant
policies** (per-org template/provider allow-lists), and **future federated templates** (a
registered `TemplateProvider`, never a boundary widening). A request may never resolve a
template outside its tenant's org.

## 14. Observability
- **Metrics** — built/validated/rendered/rejected/expired counts; build latency; per-stage
  timers; tokens used/limit; truncation count; templates resolved.
- **Tracing** — a span per stage (select → generate → budget → format → validate → render).
- **Health** — module `health()` (RuntimeModule).
- **Sub-statistics** — `TemplateStatistics`, `RenderingStatistics`, `BudgetStatistics`,
  `ValidationStatistics`, `PipelineStatistics`.
- **Replay / Gateway** — standard `prompt.*` envelopes; deterministic full-cycle replay.

## 15. Extension Points (interfaces only — no implementations)
All future coupling is a port; **no direct runtime dependency**.
- **`ProviderProfile` / `ModelProfile` / `Tokenizer`** — provider rendering conventions +
  model window + token counting (Anthropic/OpenAI/Gemini/local land as adapters).
- **`TemplateStore` / `TemplateProvider`** — durable + dynamic templates.
- **`PromptOptimizer`** — a future prompt-optimization pass (e.g. compression/rewriting),
  applied around the deterministic core; off by default.
- **`MultiModalRenderer`** — future image/audio prompt parts.
- **`PromptConsumer`** — the delivery seam for the **future Reasoning / Agent / Tool /
  Workflow / MCP / A2A** runtimes: they consume the finished `PromptPackage` (and call the
  model) — they never reconstruct a prompt.

These make Reasoning/Agent/Tool/Workflow/MCP/A2A/Optimizer/Multi-modal land as
`ProviderProfile`/`PromptConsumer`/`TemplateProvider` implementations **without redesigning**
the builder, renderer, budget, or repository.

## 16. Risks
| # | Risk | Mitigation |
|---|---|---|
| 1 | **Coupling to `context`** if Prompt imported `ContextPackage`. | Consume the **frozen `core.ports.context.AssembledContext`**; the Context→Prompt handoff is a composition-root `ContextConsumer` bridge — neither runtime imports the other. |
| 2 | **Sync↔async bridge** (`PromptPort` sync; repository async). | Proven `run_coro_sync`; local adapters never suspend; network stores use the async API. |
| 3 | **Frozen `PromptPort`/`PromptFragment` too thin** for a full package. | Rich internal `PromptPackage`; `to_fragment()` narrows to the frozen shape — additive, no port change. |
| 4 | **Provider neutrality vs determinism.** | Rendering is pure data via `ProviderProfile`; no SDK/network in-runtime; replay records the rendered package, not a provider call. |
| 5 | **Generation parameters ownership.** | Prompt **carries** params immutably; **Reasoning interprets** them — no inference in Prompt (no ownership leak). |
| 6 | **No token metering upstream yet.** | `ModelProfile`/`Tokenizer` **ports** with a deterministic default estimator; model-exact counts are additive. |
| 7 | **Event vocabulary** — only 2 prompt EventTypes exist. | Phase discriminator on `PROMPT_RENDERED` + `PROMPT_TEMPLATE_UPDATED` (established pattern); granular types deferred additive. |

## 17. Architectural Self-Review
- **Ownership:** prompt construction only; context/reasoning/inference/tools consumed or
  carried, never owned. ✔
- **Dependency direction:** `prompt → core, tenant` only; **no** import of `context`,
  `memory`, `knowledge`, any runtime, or a provider SDK (dependency inversion) — acyclic. ✔
- **Repository boundaries:** `PromptRepository` is the sole store-toucher and holds
  **temporary** packages + templates only (long-term storage stays Memory's). ✔
- **Provider neutrality:** rendering/tokenizing/model-window are injected ports; no vendor,
  no network, no LLM in-runtime. ✔
- **Replay / Gateway:** standard `prompt.*` envelopes; deterministic; bus unmodified. ✔
- **Determinism:** pure-substitution templates, content-hash ids, injected clock, stable
  ordering. ✔
- **Future compatibility:** Reasoning/Agent/Tool/Workflow/MCP/A2A/Optimizer/Multi-modal all
  land as `ProviderProfile`/`PromptConsumer`/`TemplateProvider`/`MultiModalRenderer`
  implementations — no builder/renderer/budget/repository change. ✔
- **No ownership leakage:** Prompt never owns Memory, Knowledge, Context Assembly, Reasoning,
  LLMs, Tools, Workflow, or Agents. ✔
- **Closed runtimes untouched:** Foundation + Memory + Knowledge + Context + Ports + Events +
  Repositories unchanged; the thin frozen `PromptPort`/`PromptFragment` + `AssembledContext`
  are honoured, not edited. ✔

**No future runtime requires a redesign of Prompt Runtime.** After M5.4B the Prompt Runtime
will be **architecturally closed except for additive extensions and bug fixes.**

## 18. Implementation Plan
Mirrors the Memory/Knowledge/Context core→intelligence split; each step additive within
`prompt/`, foundation untouched.

- **M5.4B-1 — Prompt Runtime Core.** Domain (`PromptSection`/`PromptPackage`/`PromptMessage`/
  `PromptLineage`) + content-hash ids + `core.fsm` lifecycle; `TemplateRegistry` +
  `TemplateResolver`; `SectionGenerator` (over the frozen `AssembledContext`); `PromptBuilder`
  + `PromptRenderer` (a default provider-neutral profile) + `PromptFormatter`; the
  **temporary** `PromptRepository` + local adapters + default `Tokenizer`; serializer, config,
  factory; `PromptRuntime` implementing `PromptPort` (deterministic build/render, naive
  budget by truncation); `prompt.*` events; core tests. *Non-goals:* budget/validation/
  policies/tenancy intelligence + provider adapters.
- **M5.4B-2 — Prompt Runtime Intelligence.** `PromptBudgetManager` (reserve/completion/
  overflow/adaptive/model-aware seam), `PromptValidationEngine`, `PromptPolicyEngine`,
  `PromptTenancy`, prompt **lineage** completion, `ProviderProfile`/`ModelProfile` seams +
  the default profiles, full observability, extension interfaces
  (`TemplateStore`/`TemplateProvider`/`PromptOptimizer`/`MultiModalRenderer`/`PromptConsumer`);
  intelligence tests + architecture-review closure.
- **M5.4C — Validation & Closure (audit-only).** Certify closed except additive
  extensions/bug fixes.

Then the Reasoning Runtime consumes the `PromptPackage` via `PromptConsumer` (and calls the
model) — domain-only on the M5.0 substrate.

---

## What landed in M5.4B-1 (core — all additive)
The Prompt Runtime **core** is implemented in `platform/prompt/`. Nothing outside
`prompt/` (+ one new test file) changed; `core`, `runtime`, `events`, `gateway`, `replay`,
`memory`, `knowledge`, and `context` are untouched. **`prompt/` imports only `core`**
(verified by an AST dependency test) — its only context input is the frozen
`core.ports.context.AssembledContext`, and provider/model concerns arrive through the
injected `ProviderProfile`/`ModelProfile`/`Tokenizer` ports, so no sibling runtime is
imported. **484 tests** pass (30 new); new code is ruff-clean.

1. **Domain (`domain.py`) + request (`request.py`) + ids (`ids.py`).** Frozen/slotted
   `PromptSection` (with `PromptLineage`: template source id+version, context source ids +
   provenance, rendering/section version, transformation, hash), `PromptPackage`,
   `PromptMessage`, `PromptResponse`, `PromptScope`, `BudgetUsage`, `PromptMetadata`,
   `PromptStatistics`; `PromptRequest` + `GenerationParameters` (carried, never interpreted).
   **Content-hash ids** (`section_id_for`/`prompt_hash_for`/`package_id_for`/
   `template_version_for`).
2. **Lifecycle (`lifecycle.py`) — dogfoods `core.fsm`.** `REQUESTED → BUILT → VALIDATED →
   RENDERED → DELIVERED` (+ `REJECTED`, `DELIVERED → EXPIRED`); illegal moves raise typed
   `FSMError`. Validation is a formal FSM stage (trivial always-pass in the core).
3. **Templates (`templates.py`).** `Template` (content-hash-versioned, pure `{{placeholder}}`
   body), `TemplateRegistry` (built-in defaults per section kind + org/user/shared
   registration), `TemplateResolver` with **deterministic precedence** request override >
   user > org > shared > built-in default. No dynamic templates (M5.4B-2+).
4. **Section generation (`sections.py`).** `SectionGenerator` maps the items of the **frozen
   `AssembledContext`** (the only context input) to typed, lineage-complete `PromptSection`s;
   it never queries Memory/Knowledge/Context.
5. **Formatting + rendering (`formatter.py`, `renderer.py`).** `PromptFormatter` (deterministic
   whitespace + stable section order); `PromptRenderer` delegates to the injected
   `ProviderProfile` to produce provider-neutral `PromptMessage`s — **no SDK, HTTP, provider,
   or LLM**.
6. **Builder (`builder.py`).** Deterministic pipeline: sections → format → **naive
   truncation budget** (drop lowest-priority *context* sections over the model window −
   completion reserve; instructions system/developer/user never dropped) → render →
   `PromptPackage`. No intelligent budgeting (M5.4B-2).
7. **Temporary repository (`repositories/`) + local adapters (`stores/`) + default
   profiles.** `PromptRepository` (the sole store-toucher; **temporary** package + template
   records) over in-memory Document/Metadata stores; the default `WordTokenizer`,
   `NeutralProviderProfile` (one message per section), and `DefaultModelProfile` (fixed
   window + completion reserve) behind the `Tokenizer`/`ProviderProfile`/`ModelProfile`
   ports.
8. **Runtime (`runtime.py`).** `PromptRuntime(BusConsumer, PromptPort)` — `build`
   (drives the lifecycle + emits `prompt.*` phases + persists), the frozen
   `render`/`register` (single-template path + template registration, emits
   `prompt.template.updated`), `get`/`remove`/`history`, `expire` (TTL sweep),
   `statistics`/`health`.
9. **Serializer / config / factory / observability / events.** `PromptSerializer`
   (round-trippable package/section/request ↔ storage + event/API views);
   `PromptConfiguration` (validated store/tokenizer/TTL/budget knobs — **no
   provider-specific config**); `PromptFactory` (composition root — the only place adapter
   kinds are named + where the profiles are injected); `Metric` names + `PromptPhase`.

### Event mapping (bus unmodified — the design's §12 realized)
Lifecycle phases ride the **existing** `PROMPT_RENDERED` (`requested`/`built`/`validated`/
`rendered`/`delivered`/`rejected`/`expired`) with a `phase` discriminator; template
registry changes ride `PROMPT_TEMPLATE_UPDATED` — no new `EventType`, no bus change
(Memory/Knowledge/Context precedent).

### Deferred to M5.4B-2 (all additive — no core shape change)
`PromptBudgetManager` (reserve/completion/overflow/adaptive/model-aware) ·
`PromptValidationEngine` (verdicts) · `PromptPolicyEngine` · `PromptTenancy` (adds the
`tenant` foundation dep) · full prompt-lineage extensions · real `ProviderProfile`/
`ModelProfile` adapters (Anthropic/OpenAI/Gemini/local) + tokenizers · `TemplateStore`/
`TemplateProvider`/`PromptOptimizer`/`MultiModalRenderer`/`PromptConsumer` extension
interfaces · observability intelligence · streaming.
