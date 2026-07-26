# Epic 6 — Execution Platform v1.0 (FROZEN)

**Tag:** `epic6-execution-platform-v1.0`
**Date:** 2026-07-26
**Status:** ✅ Production Certified · 🔒 **FROZEN**
**Baseline for:** Epic 7 and all future work.

Epic 6 builds the **execution / agent platform** on top of the Epic 5 Runtime
Platform (v1.0): the capabilities that turn a reasoning stack into an
autonomous, tool-using, multi-step agent. This document is the permanent
point-in-time snapshot — the frozen manifest, invariants, and extension policy
that every future Epic must respect.

> No runtime behaviour changed at M6.8F. This is a closure/documentation
> milestone only: audit → manifest → declaration.

---

## 1. Freeze Manifest

Certification key: 🟢 certified & production-ready · versions are the shipping
milestone (RUNTIME_VERSION where a runtime declares one). Owner is the Technical
Owner for every capability (solo-owner repository).

| # | Capability | Package | Version | Cert. | Prod-ready | Dependencies | Frozen public interface | Extension points |
|---|---|---|---|---|---|---|---|---|
| 1 | **Provider Runtime** † | `provider/` | M5.6 (Epic 5 origin) | 🟢 | Yes (offline-certified) | `core`, `tenant` | `ProviderRuntime`, `ProviderRegistry`, routing strategies, `core.ports` provider/LLM contracts | Register vendor `AsyncAdapter`/`AdapterPort`; new routing strategy; policy |
| 2 | **Tool Runtime** | `tool/` | M6.1B-3 | 🟢 | Yes | `core` | `ToolRuntime`, `ToolFactory`, `ToolRegistry`, `tool.ports.ToolAdapter`, `ToolManifest`/`ToolRequest`/`ToolResult` | `ToolAdapter` impls; `tool.extensions.MCPBridge`/`SecretsManager` seams |
| 3 | **Skill Runtime** | `skill/` | M6.2B-2 | 🟢 | Yes | `core` | `SkillRuntime`, `SkillFactory`, `SkillRegistry`, `SkillManifest` (manifest-not-code, ADR-0008) | New skill manifests; skill catalog tags |
| 4 | **Hook Framework** | `hooks/` | M6.3B | 🟢 | Yes | `core` | `HookEngine`/`HookRuntime`, `hooks` registry, ordering/filter/policy, replay | Register hooks; ordering/policy config (cross-cutting library, ADR-0010) |
| 5 | **Workflow Runtime** | `workflow/` | M6.4B-2 | 🟢 | Yes | `core` | `WorkflowRuntime`, `WorkflowFactory`, `WorkflowManifest`, durable repository seam (ADR-0009), `core.ports.tool.ToolPort` | New workflow manifests; durable repository impl; tool port wiring |
| 6 | **Agent Runtime** | `agent/` | M6.5B-3 | 🟢 | Yes | `core` | `AgentRuntime`, `AgentFactory`, dual FSM (ADR-0013), structure/intelligence/correctness split (ADR-0014), `agent`↔`workflow` port (ADR-0015) | Reasoning/structure adapters; agent policies; the workflow port seam |
| 7 | **Loop Engine** | `loop/` | M6.6B-1 | 🟢 | Yes | `core` | `run_loop` / `loop.core.pipeline`; no registrable identity (ADR-0016) | Composition only (invoked by the integration root) |
| 8 | **Platform Integration** | `integration/` | 6.7.0 (+ M6.7.1 hardening) | 🟢 | Yes | `core`, `agent`, `loop`, `skill`, `tool`, `workflow` | Composition root; `trace_id` correlation, bounded latency/replay stores, cancellation wiring (ADR-0015/0017) | New capability wiring; store sizing/TTL config |
| 9 | **MCP Runtime** | `mcp/` | 6.8.0 (+ M6.8.1 hardening) | 🟢 | Yes (offline + first-party live) ‡ | `core`, `tenant` | `MCPRuntime`, `MCPFactory`, `MCPServerRegistry`/`MCPCapabilityRegistry`, transports, `MCPTrustPolicy`, `CredentialProvider` seam | New transports; `CredentialProvider`; `MCPTrustPolicy`; the `bridge.MCPToolBridge` |
| — | **MCP↔Tool Bridge** | `bridge/` | 6.8.x | 🟢 | Yes | `core`, `mcp`, `provider`, `tool` | `MCPToolBridge`, `MCPToolRegistrar`, `ProviderModelBridge` (composition root) | New bridges only (never imported by a capability) |

**Floor packages (frozen dependencies):** `core/` (ports, FSM, errors, events,
policies — the behaviour-free contract floor) and `tenant/` (isolation context +
access interfaces). Neither imports any runtime.

† **Provider Runtime** was certified and frozen in **Epic 5 (Runtime Platform
v1.0, M5.6)**. It is Epic 6's execution boundary to external AI providers and is
re-confirmed stable here; it remains an Epic 5 asset under the same freeze.

‡ **MCP Runtime** live-validated against first-party stdio + HTTP/SSE servers
(M6.8.1). Interop against an **independent third-party** MCP server remains an
integration-environment step (the sole open readiness condition in Epic 6).

---

## 2. Repository Audit (verified 2026-07-26)

- **Tests:** `1411 passed` (full suite), ruff-clean across every Epic 6 package.
  (The only ruff findings in the tree are in the excluded `voicebot/` prototype.)
- **Dependency graph — acyclic (verified by AST scan + cycle detection):**

  ```
  core     → ∅                         tenant  → ∅            (floor packages)
  provider → core, tenant              tool    → core
  skill    → core                      hooks   → core
  workflow → core                      agent   → core
  loop     → core                      mcp     → core, tenant
  integration → core, agent, loop, skill, tool, workflow      (composition root)
  bridge      → core, mcp, provider, tool                     (composition root)
  ```

- **Ownership boundaries — unchanged.** Every certified capability imports only
  `core` (+ `tenant` for provider/mcp). No capability imports a sibling runtime.
- **No circular imports.** Cycle detection over the intra-platform edge set
  returns none.
- **Certified capabilities remain independent.** `tool`, `skill`, `workflow`,
  `agent`, `loop`, `mcp` are mutually non-importing.
- **Composition roots are the only integration points.** `integration/` and
  `bridge/` are pure sinks — **nothing imports them**. `mcp` is reachable only
  through `bridge`.
- **Replay ownership — unchanged & partitioned.** Each capability owns only its
  own frames (Tool/Workflow/Agent/Hook/MCP); MCP replay is MCP-frames-only;
  Integration correlates by `trace_id`, never by owning another's replay.
- **Observability ownership — unchanged & local.** Each capability emits its own
  events/metrics; Integration correlates by `trace_id` only.

---

## 3. Architectural Invariants (must never change)

1. **Ownership model.** One capability owns one concern. Protocol↔MCP,
   tool-invocation↔Tool, orchestration↔Workflow, planning↔Agent, iteration↔Loop,
   inference↔Provider/Reasoning. No capability assumes another's responsibility.
2. **Dependency direction.** Capabilities depend **downward only**, onto `core`
   (and the `tenant` floor). Never a sibling; never a composition root.
   `core`/`tenant` depend on nothing.
3. **Deterministic execution.** Given the same inputs and an injected clock, every
   runtime is deterministic; offline Echo adapters/servers are the default path.
4. **Replay ownership.** A capability records and re-verifies **only its own**
   hash-chained frames. Cross-capability replay is forbidden.
5. **Observability ownership.** Metrics/events are capability-local; cross-capability
   grouping is by `trace_id` correlation only.
6. **Configuration boundaries.** Config is validated fail-fast at construction;
   no hidden global mutable state; all tunables are explicit value objects.
7. **Runtime isolation.** A capability failure surfaces as its own typed error
   (under `core.errors.PlatformError`) and is normalized at the owning boundary
   (e.g. every MCP error → a failed `ToolResult` at the Tool boundary).
8. **Tenant isolation.** Work runs under a `TenantContext`/`org_id`; connections,
   credentials, and replay are never shared across tenants.
9. **Capability independence.** Capabilities are separately testable, separately
   versioned, and wired only at a composition root.

---

## 4. Extension Policy (how Epic 7+ may extend Epic 6)

**Allowed — extend, never modify:**
- **Composition** — wire capabilities together in a new/extended composition root.
- **Adapters** — implement a frozen port (`ToolAdapter`, provider `AsyncAdapter`,
  `MCPTransport`, `CredentialProvider`, durable repository).
- **Bridges** — a new `bridge/` module importing multiple capabilities (the
  `ProviderModelBridge`/`MCPToolBridge` precedent); never imported by a capability.
- **Plugins** — register new manifests (tools/skills/workflows/agents/MCP servers).
- **Configuration** — new policies, trust policies, store sizing/TTL, routing.
- **New capabilities** — a new closed capability depending only on `core`
  (+ `tenant`), wired at a composition root.

**Forbidden:**
- **Ownership changes** — moving a concern between capabilities.
- **Dependency inversions** — a capability importing a sibling or a composition root.
- **Runtime redesign** — altering a frozen FSM, contract, or ownership boundary.
- **Breaking public contracts** — changing the frozen interfaces above
  incompatibly (additive, versioned evolution only).

Rule of thumb: **future work extends Epic 6; it does not edit it.**

---

## 5. ADR Status (Epic 6)

All Epic 6 ADRs are **ratified and implemented**, with no conflicts and no
superseded decision requiring action.

| ADR | Subject | Capability | Status |
|---|---|---|---|
| ADR-0008 | Skill = manifest, not code | Skill | Ratified · Implemented |
| ADR-0009 | Workflow durable-repository exception | Workflow | Ratified · Implemented |
| ADR-0010 | Hook framework = cross-cutting library | Hooks | Ratified · Implemented |
| ADR-0012 | Tool contract = manifest + adapter | Tool | Ratified · Implemented |
| ADR-0013 | Dual FSM, goal-scoped for Agent | Agent | Ratified · Implemented |
| ADR-0014 | Structure / intelligence / correctness split | Agent | Ratified · Implemented |
| ADR-0015 | Agent↔Workflow port seam | Integration | Ratified · Implemented |
| ADR-0016 | Loop Engine has no registrable identity | Loop | Ratified · Implemented |
| ADR-0017 | Execution-context duplication + lineage | Integration | Ratified · Implemented |

**Foundational (Epic 5 / cross-epic):** ADR-0001…0007, P-ADR-0001 — ratified,
still binding, no action.

**Recommendation (no ADR created here, per scope):**
- **ADR-0011 is absent.** It was "reserved" for the MCP-as-Tool-adapter bridge;
  the code no longer references it (M6.8.1/F7 repointed the bridge to ADR-0012 +
  ADR-0015). Recommended future doc-only action: either author ADR-0011 to
  formally record the MCP↔Tool bridge decision, or renumber and note the seam as
  fully covered by ADR-0012. **No runtime impact.**

---

## 6. Versioning

- **Epic 6 Baseline Version: `Execution Platform v1.0`** (tag
  `epic6-execution-platform-v1.0`), layered on `Runtime Platform v1.0` (Epic 5).
- **Semantic versioning going forward (per capability + platform baseline):**
  - **MAJOR** — a breaking change to a frozen public contract (forbidden within a
    baseline; requires a new baseline + ADR).
  - **MINOR** — additive capability/adapter/bridge/plugin/config (the normal path).
  - **PATCH** — hardening/bug-fix with no contract change (e.g. M6.8.1).
  - The **platform baseline** (`vX.0`) increments MAJOR only when the invariants
    or the frozen interface set change; Epic 7 additions ship as **v1.x**.

---

## 7. By the numbers

| Metric | Value |
|---|---|
| Epic 6 capabilities frozen | 8 runtimes + 1 hook framework + 2 composition roots |
| Total tests | 1411 passing |
| Ruff (Epic 6 scope) | 0 errors |
| Dependency cycles | 0 |
| Capabilities importing a sibling runtime | 0 |
| Capabilities importing a composition root | 0 |
| Epic 6 ADRs (ratified + implemented) | 9 |
| Open readiness condition | 1 (third-party MCP interop, integration-env) |

---

## 8. Declaration

**EPIC 6 IS OFFICIALLY FROZEN.**

Every capability above is production-certified and stable. The invariants and
frozen interfaces in this document are the permanent execution foundation.
Future Epics **extend** this baseline through composition, adapters, bridges,
plugins, configuration, and new capabilities — never by modifying it.
