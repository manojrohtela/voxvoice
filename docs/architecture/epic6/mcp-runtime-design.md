# MCP Runtime — Design (M6.8A)

Design-only. No code. Adds one new, additive Epic 6 capability — the **MCP
Runtime**, the platform's single owner of client-side Model Context Protocol
communication with external MCP servers. It integrates through the *same*
composition-root/bridge discipline `ProviderModelBridge` already established
(ADR-0011's reserved intent), modifies no certified capability, and owns no
decision any certified capability already owns.

---

## 1. Executive Summary

- The MCP Runtime is a **closed, additive Epic 6 capability** (`mcp/`), shaped
  exactly like Tool/Skill/Workflow/Agent Runtime: manifest/descriptor
  separation, `core.fsm` lifecycles, registry/catalog separation, factory
  composition root, `mcp → core[, tenant]` only.
- It owns the **client-side MCP wire**: server registration, connection and
  session lifecycle, transport, protocol negotiation, capability discovery,
  request/response execution, streaming/notifications, health, authentication,
  reconnection, and MCP-local replay/observability/diagnostics.
- It owns **none** of planning, workflow execution, tool-execution *semantics*,
  provider routing, reasoning, memory, context, or prompt assembly.
- A remote MCP server's **tools** are surfaced to the platform as
  `mcp.<server>.<tool>` **Tool manifests paired with an MCP-backed adapter**
  (`MCPToolBridge`, implementing the Tool Runtime's own already-certified
  adapter seam) — so the entire existing Agent→Workflow→Skill→Tool path invokes
  a remote MCP tool with **zero change** to any of those runtimes. This mirrors
  `ProviderModelBridge` (Provider Runtime is the capability; the bridge exposes
  it to Reasoning via the frozen `ModelProvider` port).
- **Recommended position: Option A** — `… → Tool Runtime → MCPToolBridge → MCP
  Runtime → Remote MCP Server`. Tool Runtime stays the sole owner of "a tool
  invocation" as the platform sees it (identity, governance, timing, replay
  boundary); MCP Runtime is the sole owner of "the MCP exchange behind it."
- MCP's non-tool primitives (resources, prompts, sampling) are protocol-owned
  by the MCP Runtime and surfaced additively via thin, later bridges
  (resources→a `ContextSource`; prompts→`PromptPort`; sampling→the reverse
  Reasoning path) — never by widening a certified capability.
- Determinism is preserved by a deterministic **loopback transport +
  `EchoMCPServer`** default (no network in tests) and hash-chain MCP replay —
  the same offline-validation discipline every Epic 6 capability already uses.
- Three implementation bundles: **M6.8B-1** (core: servers/sessions/transport/
  negotiation/discovery), **M6.8B-2** (execution/resilience/auth/replay/
  observability + `MCPToolBridge`), **M6.8C** (production certification).

---

## 2. Platform Architecture

```
Client
  │
  ▼
API / Gateway (Epic 1-4, unchanged)
  │
  ▼
Agent Platform Integration (M6.7, frozen — composition root)
  │
  ▼
Agent Runtime ─▶ Workflow Runtime ─▶ Skill Runtime ─▶ Tool Runtime  (all frozen)
                                                          │
                                     ┌────────────────────┴─────────────────────┐
                                     │  ToolAdapter seam (frozen, ADR-0012)      │
                                     │   • EchoToolAdapter (local, default)      │
                                     │   • MCPToolBridge  (NEW, composition root)│
                                     └────────────────────┬─────────────────────┘
                                                          ▼
                                                   MCP Runtime (NEW, mcp/)
                                                          │  session · transport · protocol
                                                          ▼
                                                   External MCP Server(s)
```

The MCP Runtime is reached **only** through the Tool Runtime's own adapter seam
(for tools) or through equally thin, additive bridges for resources/prompts.
No certified capability imports `mcp`; only the composition root (Integration /
`bridge/`) imports both `mcp` and `tool` to wire the bridge — the exact acyclic
relationship `pipeline`/`bridge` already have.

---

## 3. Ownership Matrix

| Concern | Owner |
|---|---|
| MCP server registration / discovery of *which servers exist* | **MCP Runtime** (`MCPServerRegistry`) |
| MCP connection lifecycle (open/negotiate/ready/degraded/closed) | **MCP Runtime** (`MCPConnectionManager` + connection FSM) |
| MCP session lifecycle (initialize→ready→shutdown) | **MCP Runtime** (`MCPSessionManager` + session FSM) |
| Transport (stdio / HTTP+SSE / streamable-HTTP / loopback) | **MCP Runtime** (`MCPTransport`) |
| Protocol negotiation (version, capabilities) | **MCP Runtime** (`MCPCapabilityRegistry`) |
| MCP capability discovery (`tools/list`, `resources/list`, `prompts/list`) | **MCP Runtime** |
| MCP request/response execution (`tools/call`, `resources/read`, …) | **MCP Runtime** (`MCPRequestExecutor`) |
| MCP server health / heartbeat / ping | **MCP Runtime** (`MCPHealthMonitor`) |
| MCP authentication (OAuth2/token/mTLS handshake) | **MCP Runtime** (`MCPAuthentication`) |
| MCP connection pooling / reuse / limits | **MCP Runtime** (`MCPConnectionManager`) |
| MCP-local replay (request/response hash chain) | **MCP Runtime** (`MCPReplay`) |
| MCP-local metrics / events / connection traces | **MCP Runtime** (`MCPObservability`) — onto the existing `core.events.EventBus`, `EventSource.MCP` (already reserved) |
| MCP-local diagnostics | **MCP Runtime** (`MCPDiagnostics`) |
| **A tool invocation** (identity, arg validation, policy/approval, timing, `ToolResult`, tool replay boundary) | **Tool Runtime** (unchanged) — a `mcp.*` tool is just a manifest + the MCP-backed adapter |
| Exposing a discovered MCP tool to the platform | **`MCPToolBridge`** (new composition-root adapter implementing `tool.ports.ToolAdapter`) — owns *translation only* |
| Provider routing / model failover / completions | **Provider Runtime** (unchanged) — MCP is not a model provider |
| Reasoning / planning / workflow / skill orchestration | **Their own runtimes** (unchanged) |
| Deciding *whether/when* to call an MCP tool | **Agent/Workflow** (unchanged) — MCP Runtime never plans |
| Cross-capability trace correlation | **Integration** (M6.7, unchanged) — MCP threads the same `trace_id` field |

**The single governing rule:** MCP Runtime owns the *MCP protocol conversation*;
Tool Runtime owns *what the platform calls a tool*. The boundary is the
`ToolAdapter` seam.

---

## 4. Runtime Design

Responsibilities only (no implementation). Each mirrors an established Epic 6
component role.

| Component | Responsibility |
|---|---|
| **MCPRuntime** | The capability facade + `BusConsumer`. Composes the managers below; exposes register-server / connect / discover / execute / status / shutdown over its own public surface. Owns no protocol logic itself — delegates to the managers. Mirrors `ToolRuntime`. |
| **MCPServerRegistry** + **MCPCapabilityRegistry** | Registry: immutable, versioned `MCPServerManifest` (endpoint, transport kind, auth mode, declared capabilities) registration/discovery/namespacing/lifecycle — no connections. Catalog: the negotiated, per-session capability set discovered from a live server (`tools`/`resources`/`prompts`/`sampling` availability). Registry/catalog separation mirrors Tool's registry/catalog. |
| **MCPConnectionManager** | Owns connection objects and their FSM: open, pool, reuse, limit, and close transport connections; back-pressure and max-connections policy. Never negotiates protocol (that's the session). |
| **MCPTransport** | The pluggable wire: `stdio`, `http+sse`, `streamable-http`, and a deterministic **`loopback`** default (`EchoMCPServer`) for offline tests. A `MCPTransport` moves opaque JSON-RPC frames; it interprets none of them. Mirrors Provider's `AdapterPort`. |
| **MCPSessionManager** | Owns the session FSM: `initialize` handshake, protocol-version + capability negotiation, `ready`, `shutdown`; one session per (server, tenant) scope. Owns capability negotiation results. |
| **MCPRequestExecutor** | Owns the request/response exchange for every MCP method (`tools/call`, `resources/read`, `prompts/get`, `sampling/createMessage`), including streaming and server-initiated notifications. Deterministic given (request, transport). Never decides *which* request to make. |
| **MCPHealthMonitor** | Owns `ping`/heartbeat, liveness, degraded-server detection, and the health signals that drive reconnection policy. Emits health events; makes no failover decision outside MCP (Provider owns *its* failover; this is MCP-server health only). |
| **MCPAuthentication** | Owns the client-side auth handshake (OAuth2 authorization-code/client-credentials, bearer token, mTLS) and token refresh; reads secrets through an injected `SecretsProvider` seam — never stores raw credentials in manifests or replay. |
| **MCPReplay** | `MCPReplayRecord` (hash chain over the ordered request/response frames of one session) + a replay engine that re-verifies determinism against a recorded transcript. Replays only MCP's own exchange. |
| **MCPObservability** | `MCPConnectionTrace`/`MCPRequestTrace`, metrics, and `mcp.*` `EventType` emissions onto the existing bus. Reference/projection only. |
| **MCPDiagnostics** | Aggregates MCP's own statistics/health into an `MCPRuntimeDiagnostics` — MCP-local, never cross-capability. |
| **MCPConfiguration** | The capability's own typed config (per-transport timeouts, heartbeat interval, max reconnect attempts, connection-pool limits, negotiation policy). Constructed by `MCPFactory`. All tunables here — nothing hardcoded (Constitution §A2.6). |
| **MCPFactory** | Composition root for the capability: `default()` builds the fully-offline deterministic runtime (loopback transport + `EchoMCPServer`, in-memory stores), matching every capability's own offline default. |

---

## 5. Session & Protocol Model

### 5.1 Lifecycles (two FSMs, ownership explicit)

- **Connection FSM** (`MCPConnectionManager`): `DISCONNECTED → CONNECTING →
  CONNECTED → DEGRADED → RECONNECTING → CLOSED`. Owns transport-level liveness.
- **Session FSM** (`MCPSessionManager`): `CREATED → INITIALIZING →
  NEGOTIATING → READY → DRAINING → CLOSED`. Owns protocol state on top of a
  live connection. The dual-FSM shape mirrors Tool/Workflow Runtime.

| Concern | Owner |
|---|---|
| Connection open/close/pool | `MCPConnectionManager` |
| `initialize` handshake + version/capability negotiation | `MCPSessionManager` |
| Auth handshake + token refresh | `MCPAuthentication` (invoked during `INITIALIZING`) |
| Heartbeat / ping | `MCPHealthMonitor` |
| Reconnection policy (attempts, backoff schedule) | `MCPConnectionManager` — **deterministic, config-driven**, never random |
| Timeout handling (per request / per handshake) | `MCPRequestExecutor` (request) / `MCPSessionManager` (handshake) — surfaced as typed `MCPTimeoutError` |
| Retry ownership | **Within an MCP session**, `MCPRequestExecutor` may retry an idempotent MCP frame per config. **Cross-capability retries stay owned by Reasoning's `RetryController` / Provider's `FailoverController`** (unchanged) — MCP never adds a second retry loop above the Tool boundary. |
| Shutdown / drain | `MCPSessionManager` (`DRAINING`) then `MCPConnectionManager` (`CLOSED`) |

### 5.2 Protocol (respect the MCP spec — do not redesign it)

MCP is JSON-RPC 2.0 over a transport. The MCP Runtime models the spec's own
message shapes as immutable, provider-neutral records — it never invents a
protocol:

- **Request/Response/Notification**: `MCPRequest` (method, params, id),
  `MCPResponse` (result | error), `MCPNotification` (method, params, no id).
- **Lifecycle methods**: `initialize`, `initialized`, `ping`, `shutdown`.
- **Discovery**: `tools/list`, `resources/list`, `resources/templates/list`,
  `prompts/list`.
- **Execution**: `tools/call`, `resources/read`, `resources/subscribe`,
  `prompts/get`, `completion/complete`.
- **Sampling (reverse)**: `sampling/createMessage` — a *server→client* request
  for an LLM completion; the MCP Runtime receives it and routes it (via a
  narrow, additive bridge) to the platform's own Reasoning/Provider path,
  returning the result to the server. This is the one flow where MCP touches
  Reasoning — bridged, never owned.
- **Streaming**: partial results / progress notifications are modeled as an
  ordered `MCPResponseChunk` stream, mirroring Provider's `ResponseChunk`.

Structured content (text/JSON/blob resources) is carried opaquely and mapped at
the bridge boundary — MCP Runtime never interprets tool semantics.

---

## 6. Integration Design

**Which runtime invokes MCP:** the **Tool Runtime**, via its own adapter seam.
When an MCP server is registered and its `tools/list` discovered, the
composition root registers, for each remote tool, a `ToolManifest` in namespace
`mcp.<server>.<tool>` paired with a single shared `MCPToolBridge` adapter
(`tool.ports.ToolAdapter`). Agent/Workflow/Skill resolve and call these exactly
like any other tool; the adapter translates the `ToolRequest` into an
`MCPRequestExecutor.tools/call` and the `MCPResponse` back into a
`ToolExecutionOutcome`. Because the certified `ToolAdapter` is documented as
synchronous with async/network adapters reserved to the Tool Runtime's own
extension seam, `MCPToolBridge` plugs into that **already-provided** async
adapter extension — no Tool Runtime change.

| Boundary | Contract |
|---|---|
| Tool Runtime → MCP | `MCPToolBridge` implements the frozen `ToolAdapter`; Tool Runtime owns identity/validation/policy/timing/`ToolResult`; MCP owns the wire. |
| Agent/Workflow → MCP | **Never directly** — always through Tool Runtime (preserves governance, capability resolution, and the tool replay boundary). |
| Pipeline/Provider → MCP | Only the reverse **`sampling`** flow: an MCP server asks for a completion; a narrow bridge routes it through `pipeline.RuntimePipeline`/Provider. MCP never routes providers. |
| Platform Integration → MCP | Integration wires the `MCPToolBridge` and threads the one `trace_id`; it owns no MCP logic. |

**Who owns failures:** MCP transport/protocol/auth/timeout errors are typed MCP
errors (§9). At the Tool boundary they are translated into the Tool Runtime's
own failure result (an errored `ToolResult`) — exactly as any adapter failure
is today; Tool Runtime remains the failure owner the platform sees. Above that,
Agent's revise-or-fail policy is unchanged.

**Replay boundaries:** MCP replays only its own request/response transcript;
Tool Runtime replays only its own `ReplayRecord` (deterministic vs. the adapter,
now possibly `MCPToolBridge` in a *recorded* mode); neither inspects the other.
Cross-capability correlation is the shared `trace_id` (M6.7 `PlatformReplay
Correlation`), never a shared replay engine.

**Observability boundaries:** MCP emits `mcp.*` events; the pre-existing
top-level `replay/` platform indexes them for free (additive, AR-32); M6.7's
`PlatformCorrelation` groups them by `trace_id`. No duplicate telemetry.

---

## 7. Replay

**MCP Runtime owns only MCP replay.** `MCPReplayRecord` = a hash chain over the
ordered `(request_frame_hash, response_frame_hash)` pairs of one session, plus
the negotiated capability set — pure recomputation over MCP's own recorded
frames, never a live re-call, never inspecting Tool/Workflow/Agent state.
Determinism is verified against the deterministic loopback `EchoMCPServer`
(mirrors Tool's "deterministic replay vs. `EchoToolAdapter` only"). A recorded
transcript lets `MCPToolBridge` replay a past MCP tool call deterministically
for Tool Runtime's own replay without re-hitting the network. It **never**
replays another capability's state (verified by the capability's own dependency
test importing nothing outside `mcp`).

---

## 8. Observability

- **Events:** new `mcp.*` `EventType` members (session opened/negotiated/closed,
  request sent/succeeded/failed, server degraded/recovered) under the
  already-reserved `EventSource.MCP`. Additive; the `replay/` platform consumes
  them with no change.
- **Traces:** `MCPConnectionTrace` (per connection) + `MCPRequestTrace` (per
  request), carrying the shared `trace_id`.
- **Metrics:** MCP-local `MCPStatistics` (requests, latency percentiles, error
  rates, reconnects, active sessions) — computed by MCP only; M6.7
  `PlatformMetrics` references them, never recomputes.
- **Latency:** per-request and per-handshake taps shipped in the same code path
  as the behavior (AR-29). **Ownership stays local to MCP.**

---

## 9. Error Model

One typed hierarchy under `core.errors.PlatformError` (Constitution §A2.5),
owned by MCP:

```
MCPError(PlatformError)
├── MCPTransportError        # connection refused / dropped / TLS failure
├── MCPProtocolError         # malformed frame, version mismatch, bad negotiation
├── MCPAuthenticationError   # handshake/refresh failure, expired credential
├── MCPTimeoutError          # handshake or request deadline exceeded
├── MCPServerError           # a well-formed JSON-RPC error result from the server
└── MCPCapabilityError       # requested method the server never advertised
```

| Error | Local vs. propagate | Retry boundary |
|---|---|---|
| `MCPTransportError` | Local; drives `MCPConnectionManager` reconnection (deterministic backoff). Propagates to the Tool boundary only after reconnection budget is exhausted. | MCP connection layer |
| `MCPTimeoutError` | Local first (bounded in-session retry for idempotent frames); then to Tool boundary. | `MCPRequestExecutor` |
| `MCPAuthenticationError` | Local refresh attempt; on failure, propagates (typed) to the Tool boundary. | `MCPAuthentication` |
| `MCPProtocolError` / `MCPCapabilityError` | Fail fast, propagate — never retried (a determinism/contract violation). | none |
| `MCPServerError` | Propagates as an errored `ToolResult` (the server's own error is data, not a transport fault). | none |

At the Tool boundary every MCP error becomes a normal errored `ToolResult`
(Tool Runtime owns the platform-visible failure); MCP errors never leak past
Tool Runtime as raw exceptions. No new retry loop is introduced above Tool.

---

## 10. Dependency Graph

```
mcp            →  core [, tenant]                      (closed capability, like tool/skill/workflow)
MCPToolBridge  →  core, tool, mcp                      (composition-root adapter, like ProviderModelBridge)
integration    →  … , mcp (only to wire MCPToolBridge) (composition root; M6.7 extended additively)

tool     ⇥  mcp          (never — Tool imports no MCP; the bridge is external)
agent    ⇥  mcp          (never)
workflow ⇥  mcp          (never)
provider ⇥  mcp          (never)
mcp      ⇥  tool/agent/workflow/provider/pipeline/reasoning (never — mcp imports no sibling)
```

- `mcp` imports only `core` (+ `tenant` for isolation) — identical to every
  certified capability.
- No certified capability gains a dependency on `mcp`. The only new edges are
  the composition-root ones (`MCPToolBridge`, and Integration wiring it) — the
  same shape `bridge.ProviderModelBridge` already has. **Acyclic — verified by
  design; enforced by the same AST dependency tests.**

---

## 11. Security

| Concern | Design |
|---|---|
| Authentication | `MCPAuthentication` performs the client handshake (OAuth2 auth-code/client-credentials, bearer, mTLS); tokens obtained through an injected `SecretsProvider` seam. |
| Authorization | Which servers/tools a tenant may reach is a **Tool Runtime policy** decision at the `mcp.*` manifest (its certified `PolicyEngine`/`ApprovalEngine`), plus MCP-server allow-listing in the `MCPServerManifest`. MCP Runtime enforces server-reachability; Tool Runtime enforces per-call policy. |
| Credential handling | Secrets never appear in `MCPServerManifest`, `MCPReplayRecord`, traces, or events (redaction at the observability boundary); flagged if found (AR-20). |
| TLS / transport security | Owned by `MCPTransport` per transport kind; mTLS certificates via the secrets seam. |
| Server trust | Explicit registration only — an unregistered endpoint is unreachable; optional server-identity pinning in the manifest. |
| Session isolation | One session per (server, `TenantContext`) scope; no cross-tenant connection reuse (`MCPConnectionManager` keys the pool by tenant). |
| Multi-tenant isolation | `tenant.TenantContext` threaded through every session; `Authorizer`/`QuotaEnforcer`/`AuditSink` hooks run before connect/execute — the same pattern Context/Memory/Knowledge already use. |

---

## 12. Sequence Diagrams

**Successful MCP tool invocation**
```
Agent→Workflow→Skill→Tool: invoke(mcp.crm.lookup, args)
Tool Runtime: validate + policy (ALLOW) → ToolRequest
Tool Runtime → MCPToolBridge.execute(ToolRequest)
MCPToolBridge → MCPRequestExecutor: tools/call(name, args)  [reuses ready session]
MCPRequestExecutor → Transport → Server → MCPResponse(result)
MCPToolBridge: MCPResponse → ToolExecutionOutcome
Tool Runtime: normalize → ToolResult(ok) ; emit tool.* + mcp.* events (shared trace_id)
```

**Server discovery + capability negotiation**
```
Composition root: MCPRuntime.register(MCPServerManifest)
MCPConnectionManager: DISCONNECTED→CONNECTING→CONNECTED
MCPSessionManager: initialize → NEGOTIATING (version + capabilities) → READY
MCPRuntime: tools/list, resources/list, prompts/list → MCPCapabilityRegistry
Composition root: for each tool → register ToolManifest(mcp.<server>.<tool>) + MCPToolBridge
```

**Authentication**
```
MCPSessionManager(INITIALIZING) → MCPAuthentication: obtain token (SecretsProvider)
MCPAuthentication → auth server (OAuth2) → access_token (+refresh)
MCPSessionManager: attach credential → initialize → READY
[on 401 later] MCPAuthentication: refresh; on failure → MCPAuthenticationError
```

**Reconnect**
```
MCPHealthMonitor: ping timeout → server DEGRADED (mcp.server.degraded)
MCPConnectionManager: CONNECTED→DEGRADED→RECONNECTING (deterministic backoff schedule)
  success → re-initialize session → READY (mcp.server.recovered)
  budget exhausted → CLOSED → MCPTransportError propagates to Tool boundary
```

**Timeout**
```
MCPRequestExecutor: tools/call, deadline exceeded → MCPTimeoutError
  idempotent + budget left → bounded in-session retry
  else → errored ToolResult at the Tool boundary
```

**Cancellation**
```
Integration.execute cancelled → IntegrationCancellationToken.cancel (M6.7)
Tool Runtime: its own ToolCancellationToken (chained) → MCPToolBridge
MCPRequestExecutor: send JSON-RPC $/cancelRequest (notification) + abandon await
MCP session returns to READY; no orphaned frame
```

**Replay**
```
Operator → MCPReplay.validate(MCPReplayRecord): recompute frame hash chain
  (pure over recorded frames; no live server call)
Cross-capability: replay/ EventCorrelator groups mcp.* + tool.* by shared trace_id
```

**Shutdown**
```
MCPRuntime.shutdown → MCPSessionManager: DRAINING (finish in-flight, refuse new)
  → shutdown method → CLOSED ; MCPConnectionManager closes transport ; pool cleared
```

---

## 13. Risks

- **Architectural:** MCP resources/prompts have no single obvious platform
  owner (resources overlap Knowledge/Context; prompts overlap Prompt). *Mitigation:*
  Phase-1 surfaces **tools only**; resources/prompts are protocol-owned by MCP
  and bridged later, one additive bridge each — never widening a certified
  capability.
- **Determinism (operational):** real MCP servers are non-deterministic and
  network-bound, unlike every prior offline-validated capability. *Mitigation:*
  a deterministic `loopback`/`EchoMCPServer` default + recorded-transcript
  replay; real transports are an opt-in profile, benchmarked separately (AR-30).
- **Security:** credential handling and server trust are a larger attack surface
  than any prior capability (which had no external network). *Mitigation:*
  secrets-seam only, manifest allow-listing, per-tenant session isolation,
  redaction at the observability boundary; a dedicated security review gate in
  M6.8C.
- **Performance:** network latency + connection churn dominate; a poor pool /
  reconnect policy could stall the Tool boundary. *Mitigation:* config-driven
  pool limits, deterministic backoff, health-driven circuit behavior local to
  MCP; timeouts bounded and typed.
- **Reverse `sampling` flow:** server-initiated LLM calls invert the usual
  dependency and could pull MCP toward owning Reasoning. *Mitigation:* a narrow,
  explicitly-scoped bridge to `pipeline.RuntimePipeline`; MCP owns the frame,
  not the inference.
- **Extensibility:** the MCP spec evolves (new methods/versions). *Mitigation:*
  version negotiation is first-class (`MCPCapabilityRegistry`); unknown methods
  fail as typed `MCPCapabilityError`, never silently.

---

## 14. ADR Recommendations (recommended only — not written)

1. **MCP Runtime is a closed, additive Epic 6 capability** (`mcp → core[,
   tenant]`), not a bridge-only shim — it owns the full client protocol
   lifecycle; the bridge only exposes it. (Refines the reserved ADR-0011.)
2. **A remote MCP tool is surfaced as a `mcp.*` Tool manifest + `MCPToolBridge`
   adapter**, never by Agent/Workflow calling MCP directly — preserving the Tool
   Runtime's ownership of "a tool invocation" (ratifies ADR-0011's bridge
   posture, extends ADR-0012's manifest+adapter contract).
3. **MCP `sampling` is bridged to the platform's own Reasoning path**, never
   owned by MCP — the one sanctioned reverse edge.
4. **MCP replay is a per-session frame hash chain, validated only against the
   deterministic loopback server** — extending the "deterministic replay vs.
   Echo adapter" precedent (ADR-0016 sibling).
5. **MCP is the platform's first capability with a real external-network,
   credentialed transport** — mandating the secrets-seam + per-tenant session
   isolation as an architectural invariant.

---

## 15. Bundle Breakdown (exactly three)

| Bundle | Scope (clear ownership, no overlap) |
|---|---|
| **M6.8B-1 — Core: Servers, Sessions, Transport, Negotiation, Discovery** | `mcp/` package: `MCPServerManifest`/descriptor + `MCPServerRegistry`/`MCPCapabilityRegistry`; connection + session FSMs (`core.fsm`); `MCPConnectionManager`; `MCPTransport` with the deterministic **`loopback`/`EchoMCPServer`** default; `MCPSessionManager` (initialize + version/capability negotiation); discovery (`tools/list`/`resources/list`/`prompts/list`); `MCPConfiguration`/`MCPFactory`; temporary repository + serializer. **No execution, no auth, no health, no bridge** — protocol handshake + discovery only, fully offline. |
| **M6.8B-2 — Execution, Resilience, Auth, Replay, Observability + Tool Bridge** | `MCPRequestExecutor` (`tools/call`/`resources/read`/`prompts/get`/`sampling`, streaming + notifications); `MCPHealthMonitor` (ping/heartbeat/degraded); reconnection/backoff/timeout; `MCPAuthentication` (OAuth2/token/mTLS via secrets seam); `MCPReplay` (`MCPReplayRecord` + engine); `MCPObservability` (`mcp.*` events/traces/metrics) + `MCPDiagnostics`; **`MCPToolBridge`** (the composition-root `ToolAdapter`) + registration of `mcp.*` tool manifests; M6.7 Integration wiring (thread `trace_id`, chain cancellation). Smoke-test the full Agent→Tool→MCP→loopback path end-to-end. |
| **M6.8C — Production Certification** | Independent audit mirroring every prior Epic 6 certification: architecture/dependency/ownership/determinism/replay/observability/performance/**security** (the new external-network attack surface) and documentation — with particular attention to credential handling, per-tenant isolation, deterministic reconnection, and the Tool-boundary failure translation. |

---

## Recommendation

Approve this design for implementation starting at **M6.8B-1 — Core: Servers,
Sessions, Transport, Negotiation, Discovery**, per the three-bundle breakdown.
The MCP Runtime is a closed, additive capability (`mcp → core[, tenant]`) that
integrates through the certified Tool Runtime's own adapter seam via a new
`MCPToolBridge` — the `ProviderModelBridge` precedent applied to Tool — so no
certified capability is redesigned, the dependency graph stays acyclic, and MCP
remains independently testable, certifiable, observable, and replayable.
