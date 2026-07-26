"""MCPToolBridge (M6.8B-2) — the composition-root seam exposing MCP-discovered
tools to the certified Tool Runtime.

Mirrors `ProviderModelBridge` exactly, applied to Tool: a remote MCP tool is
surfaced as an `mcp.<server>.<tool>` `ToolManifest` paired with this single
`ToolAdapter`, so the entire Agent→Workflow→Skill→Tool path invokes a remote MCP
tool with **zero change** to any of those runtimes. This is a **separate,
top-level `bridge/` module** — neither `tool` nor `mcp` imports the other; only
this composition-root module imports both (design §6; ADR-0012 tool
contract manifest+adapter; ADR-0015 agent↔workflow port seam).

Responsibilities: **translation only** — `ToolRequest → MCP tools/call →
ToolResult`. No planning, no orchestration, no policy (those stay in Agent /
Workflow / Tool Runtime). MCP failures propagate as typed errors; the Tool
Runtime normalizes them into a failed `ToolResult` at its own boundary.
"""

from __future__ import annotations

from mcp import MCPRuntime, MCPServerError
from tool import Classification, ToolManifest, ToolRuntime, ToolUsage
from tool.domain import ToolRequest
from tool.extensions import MCPBridge
from tool.ports import ToolAdapter, ToolExecutionOutcome

_MCP_PREFIX = "mcp."


def _parse_server_namespace(tool_namespace: str) -> tuple[str, str]:
    """`"mcp.external.crm"` -> `("external", "crm")`. Raises `ValueError` on a
    non-MCP namespace (the bridge only serves `mcp.*` tools)."""
    if not tool_namespace.startswith(_MCP_PREFIX):
        raise ValueError(f"not an MCP tool namespace: {tool_namespace!r}")
    rest = tool_namespace[len(_MCP_PREFIX):]
    server_ns, sep, server_id = rest.partition(".")
    if not sep or not server_ns or not server_id:
        raise ValueError(f"malformed MCP tool namespace: {tool_namespace!r}")
    return server_ns, server_id


class MCPToolBridge(ToolAdapter):
    """A `ToolAdapter` backed by the MCP Runtime. Synchronous, mirroring the
    seam's shape (the deterministic loopback never suspends; real transports
    block on their own I/O)."""

    name = "mcp"
    capabilities = frozenset({"mcp"})

    def __init__(self, runtime: MCPRuntime) -> None:
        self._runtime = runtime

    def execute(self, request: ToolRequest) -> ToolExecutionOutcome:
        server_ns, server_id = _parse_server_namespace(request.namespace)
        # F2: the Tool seam carries `org_id`; thread it so the MCP Runtime keys the
        # connection/credential/replay per tenant — no cross-tenant sharing.
        result = self._runtime.call_tool(
            server_ns, server_id, request.tool, request.arguments,
            trace_id=request.trace_id, session_id=request.session_id, org_id=request.org_id,
        )
        if result.is_error:
            # A tool-level error the server reported → fail at the Tool boundary.
            raise MCPServerError(f"MCP tool {request.tool!r} reported an error")
        output = result.text if result.text else dict(result.raw)
        return ToolExecutionOutcome(output=output, usage=ToolUsage(duration_ms=0.0, units=1))


class MCPToolRegistrar(MCPBridge):
    """Registers negotiated MCP tools into a `ToolRuntime` as `mcp.<server>.<tool>`
    — implements the certified `tool.extensions.MCPBridge` seam. Deterministic
    and immutable after negotiation; a reconnect re-registers a fresh snapshot.
    Owns registration translation only."""

    def __init__(
        self,
        tool_runtime: ToolRuntime,
        mcp_runtime: MCPRuntime,
        bridge: MCPToolBridge,
        *,
        servers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._tool_runtime = tool_runtime
        self._mcp_runtime = mcp_runtime
        self._bridge = bridge
        self._servers = servers

    def register_external_tools(self) -> None:
        for namespace, id_ in self._servers:
            self.register_server(namespace, id_)

    def register_server(self, namespace: str, id_: str, *, trace_id: str = "") -> tuple[str, ...]:
        """Connect, discover the server's tools, and register each as
        `mcp.<namespace>.<id>.<tool>` (deterministic, version-aware). Returns the
        registered qualified tool names."""
        server = self._mcp_runtime.get_server(namespace, id_)
        tools = self._mcp_runtime.list_tools(namespace, id_, trace_id=trace_id)
        tool_namespace = f"{_MCP_PREFIX}{server.qualified_name}"
        registered: list[str] = []
        for tool in tools:
            tool_name = str(tool.get("name", ""))
            if not tool_name:
                continue
            manifest = ToolManifest(
                name=tool_name, namespace=tool_namespace, version=server.version,
                description=str(tool.get("description", "")),
                capabilities=frozenset({tool_name}),
                inputs=dict(tool.get("inputSchema", {})),
                classification=Classification.READ_ONLY,
                tags=frozenset({"mcp"}),
                metadata={"mcp_server": server.qualified_name},
            )
            self._tool_runtime.register(manifest, self._bridge)
            self._tool_runtime.activate(tool_namespace, tool_name, server.version)
            registered.append(f"{tool_namespace}.{tool_name}")
        return tuple(registered)
