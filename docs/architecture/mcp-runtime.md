# MCP Runtime (`platform/mcp/`)

**Status:** M6.8B-1 + M6.8B-2 shipped; **M6.8.1 operational hardening (F1–F7)
shipped** — external-network readiness resolving the M6.8C certification findings.
**Design authority:**
[`epic6/mcp-runtime-design.md`](epic6/mcp-runtime-design.md) (M6.8A, approved).
Component spec (Constitution §A11.3) for the *implemented* runtime — it never
supersedes or contradicts the design.

A new, **additive** closed Epic 6 capability: the platform's single owner of
client-side Model Context Protocol communication with external MCP servers.
Built on the same closed-runtime shape as Tool/Skill/Workflow Runtime
(manifest/descriptor separation, `core.fsm` lifecycles, registry/catalog
separation, factory composition root). `mcp → core` only.

## What Bundle 1 delivers

| Area | Artifacts |
|---|---|
| Server model | `MCPServerManifest`, `MCPServer`, `MCPServerIdentity`, `MCPServerDescriptor`, `MCPTransportDescriptor`, `MCPTransportKind` |
| Registry / catalog | `MCPServerRegistry` (declared servers, deterministic lookup, immutable registration) + `MCPCapabilityRegistry` (discovered/negotiated snapshots) |
| Connection lifecycle | `MCPConnectionManager` + connection FSM (`REGISTERED→CONNECTING→CONNECTED→DEGRADED→DISCONNECTED→CLOSED`) |
| Session lifecycle | `MCPSessionManager` + session FSM (`CREATED→INITIALIZING→NEGOTIATING→READY→CLOSING→CLOSED`) |
| Transport | `MCPTransport` abstraction + `LoopbackTransport` + `EchoMCPServer` (deterministic, offline, no external dependency) |
| Negotiation | `negotiate` + one-time immutable `MCPCapabilitySnapshot`; reconnect ⇒ new snapshot (no lazy renegotiation) |
| Composition | `MCPFactory` / `MCPBuilder` / `MCPConfigurationValidator`, `MCPConfiguration` (self-validating), `MCPRuntime` facade, `MCPRuntimeStatistics` |

## What Bundle 2 adds (M6.8B-2)

| Area | Artifacts |
|---|---|
| Protocol | `protocol.py` — JSON-RPC `MCPRequest`/`MCPResponse`/`MCPNotification`/`MCPResponseChunk`/`MCPToolCallResult` + `MCPMethod` |
| Execution | `MCPRequestExecutor` — tools/list\|call, resources/list\|read, prompts/list\|get, sampling/createMessage, notifications, cancel, streaming (capability-guarded; protocol execution only) |
| Transport | `MCPTransport` extended (`request`/`notify`/`stream`); `EchoMCPServer` full dispatch + fault injection; real `StdioTransport`/`HttpSseTransport`/`StreamableHttpTransport` (stdlib-only, config-selected) |
| Auth | `MCPAuthentication` (bearer/oauth2/mtls) via injected `CredentialProvider`/`StaticCredentialProvider` — never reads secrets directly |
| Resilience | `MCPHealthMonitor` — ping, deterministic exponential backoff, bounded reconnect |
| Replay | `MCPReplay` — per-session frame hash chain (`MCPReplayRecord`/`MCPSessionRecorder`/`MCPReplayEngine`); MCP frames only; loopback-validated |
| Observability | `MCPObservability` — local `mcp.*` events (`MCPEventType`) + `MCPMetrics`; `MCPDiagnostics` |
| Errors | typed `MCPError` tree (transport/protocol/auth/timeout/capability/server) under `PlatformError` |
| Tool bridge | `bridge.MCPToolBridge` (impl `tool.ports.ToolAdapter`) + `bridge.MCPToolRegistrar` (impl `tool.extensions.MCPBridge`) → `mcp.<server>.<tool>` registration + `ToolRequest→tools/call→ToolResult` translation |

**End-to-end:** Tool Runtime → `MCPToolBridge` → MCP Runtime → `EchoMCPServer`
verified — a remote MCP tool executes as an ordinary registered Tool, so the
certified Agent→Workflow→Skill→Tool chain reaches it unchanged.

**Error ownership:** every MCP error propagates as a typed `MCPError` and becomes
a failed `ToolResult` at the Tool boundary (Tool Runtime stays the platform-visible
failure owner). MCP owns protocol execution only — never planning/workflow/
tool-lifecycle/reasoning/provider-routing.

## What M6.8.1 hardens (external-network readiness, F1–F7)

| Finding | Resolution |
|---|---|
| **F1** auth wiring | `MCPTransport.apply_credential` attaches bearer/OAuth2 `Authorization` headers (HTTP frames) / passes them to a stdio child as env vars / loads mTLS cert+key into an explicit TLS context; the runtime applies the resolved credential before the handshake. Credentials still come **only** from the injected `CredentialProvider`; `MCPCredential.__repr__` redacts — never in logs/replay/observability. |
| **F2** tenant isolation | The active connection is keyed by `(tenant, server)` — `org_id` (the field the certified Tool seam carries) or an optional `TenantContext`. No connection, credential, or replay transcript is shared across tenants. `mcp → tenant` (a stdlib-only floor package; no cycle). |
| **F3** timeout | `request_timeout_s` enforced on every transport (stdio via `select`, HTTP via `urlopen` timeout) → typed `MCPTimeoutError`; never hangs. The timeout path emits `mcp.failure` + metric and records no partial replay frame. |
| **F4** id correlation | `_parse_response` requires the wire `id` to equal the request id — mismatched / missing / duplicate / late responses are rejected, never silently re-attributed. |
| **F5** trust model | `MCPTrustPolicy` — endpoint allow-list, explicit TLS-verification stance, CA bundle, mTLS requirement, https-only default — enforced fail-closed in `real_transport_for`; a non-offline config now *requires* an explicit trust policy. |
| **F6** streaming/notify | Incremental SSE / streamable-http framing (`parse_sse_stream`, deterministic + graceful terminal); notifications expect no body (202/empty = success). |
| **F7** docs | Removed the dangling ADR-0011 reference in the Tool bridge (now cites ADR-0012 + ADR-0015). |

**Live validation:** a real stdio MCP server (subprocess) and a real HTTP/SSE MCP
server (in-process, loopback) drive the hardened transports end-to-end —
credentials on the wire, request timeout, id-mismatch rejection, SSE streaming,
and body-less notify — in `tests/test_mcp_hardening.py` (all offline; no external
network). A **third-party** MCP server remains an integration-environment step.

## Dependency graph (verified acyclic)

```
mcp    → core, tenant          (+ stdlib: json/subprocess/urllib/shlex/ssl/select/os for real transports)
bridge → core, tool, mcp       (composition root — MCPToolBridge; the ProviderModelBridge pattern)
```
`tenant` is a stdlib-only floor package that imports no runtime (no cycle). No
certified capability imports `mcp`; `mcp` imports no sibling runtime; only the
composition-root `bridge/` imports both `tool` and `mcp`. Enforced by AST boundary
tests in `tests/test_mcp_runtime.py` and `tests/test_mcp_execution.py`.

## Design fidelity notes

- **Dual FSM** (connection + session) mirrors Tool/Workflow Runtime.
- **Deterministic offline default** (loopback + `EchoMCPServer`) matches the
  `EchoToolAdapter`/`EchoProviderAdapter` precedent — the first external-network
  capability stays offline-testable and replayable.
- **Real transports** (stdio/http-sse/streamable-http) are stdlib-only,
  config-selected behind `MCPTransport`; `offline_mode` forbids a non-loopback
  kind. Their wire I/O is exercised against a real server (integration), not the
  offline suite.
- **Typed `MCPError` tree** (Bundle 2) covers runtime protocol failures; config
  validation still uses `core.errors.ConfigError`. Every MCP error becomes a
  failed `ToolResult` at the Tool boundary.
- **`MCPTransport` extended additively** in Bundle 2 with `request`/`notify`/
  `stream` (Bundle 1 shipped handshake-only) — not a redesign.

## Verification

- **Tests:** `tests/test_mcp_runtime.py` (25, core) + `tests/test_mcp_execution.py`
  (32, execution/auth/health/replay/observability/errors/bridge/e2e/boundaries) +
  `tests/test_mcp_hardening.py` (20, M6.8.1 F1–F6 incl. live stdio + HTTP servers).
  Full repository suite: **1411 passed**.
- **Ruff:** clean across all source packages.
- **Dependency validation:** `mcp` external imports = `{core, tenant}` (+ stdlib); no
  certified capability imports `mcp`; `bridge` wires both; acyclic.
- **End-to-end:** Tool Runtime → `MCPToolBridge` → MCP Runtime → `EchoMCPServer`
  returns a successful `ToolResult`.
