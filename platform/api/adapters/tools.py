"""Production tool executors (M8.14).

Executors for each Tool Framework (M8.11) category, implementing the M8.12 `ToolExecutor`
interface. HTTP/Webhook go over the injected `Transport`; Function/Custom call a registered
in-process callable; Database/MCP go over injected connector seams (deterministic fakes in
M8.14 — **no real DB, no real MCP, no external system**). The dispatcher picks an executor by
the tool's category and applies the tool's **timeout + retry policy** via the resilience layer,
respecting ordering / enable / config-override / dependencies (which the plan already resolved).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api.foundation import ToolExecutor

from .resilience import RetrySpec, execute_with_resilience
from .transport import ProviderRequest, Transport


class HttpToolExecutor(ToolExecutor):
    """Calls the configured HTTP endpoint via the transport."""

    category = "http_api"

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def invoke(
        self, tool_id: str, *, arguments: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = config or {}
        req = ProviderRequest(
            provider="tool:http", method=str(config.get("method", "GET")),
            url=str(config.get("url", "")), headers=dict(config.get("headers", {})),
            body=dict(arguments or {}),
        )
        resp = self._transport.request(req)
        return {"tool_id": tool_id, "status": "ok", "output": resp.json.get("reply", resp.text),
                "http_status": resp.status, "latency_ms": resp.latency_ms}


class WebhookToolExecutor(HttpToolExecutor):
    """A webhook is an HTTP POST; reuse the HTTP executor with POST default."""

    category = "webhook"

    def invoke(self, tool_id, *, arguments=None, config=None) -> dict[str, Any]:
        config = {"method": "POST", **(config or {})}
        out = super().invoke(tool_id, arguments=arguments, config=config)
        out["kind"] = "webhook"
        return out


class FunctionToolExecutor(ToolExecutor):
    """Calls a registered in-process function by name (deterministic; no external system)."""

    category = "function"

    def __init__(self, registry: dict[str, Callable[..., Any]] | None = None) -> None:
        self._registry = registry or {}

    def invoke(self, tool_id, *, arguments=None, config=None) -> dict[str, Any]:
        config = config or {}
        fn = self._registry.get(str(config.get("function", tool_id)))
        output = fn(**(arguments or {})) if fn else f"function::{tool_id}"
        return {"tool_id": tool_id, "status": "ok", "output": output, "kind": "function"}


class DatabaseToolExecutor(ToolExecutor):
    """Runs a query via an injected connector seam (a deterministic fake in M8.14; a real driver
    in production). **No real database is opened here.**"""

    category = "database"

    def __init__(self, connector: Callable[[str, dict], Any] | None = None) -> None:
        self._connector = connector or (lambda q, cfg: f"db-rows::{len(q)}")

    def invoke(self, tool_id, *, arguments=None, config=None) -> dict[str, Any]:
        config = config or {}
        query = str(config.get("query", ""))
        rows = self._connector(query, config)
        return {"tool_id": tool_id, "status": "ok", "output": rows, "kind": "database"}


class McpToolExecutor(ToolExecutor):
    """Invokes an MCP tool via an injected connector seam (deterministic fake in M8.14). **No
    real MCP server is contacted.**"""

    category = "mcp"

    def __init__(self, connector: Callable[[str, dict], Any] | None = None) -> None:
        self._connector = connector or (lambda name, args: f"mcp::{name}")

    def invoke(self, tool_id, *, arguments=None, config=None) -> dict[str, Any]:
        config = config or {}
        result = self._connector(str(config.get("mcp_tool", tool_id)), dict(arguments or {}))
        return {"tool_id": tool_id, "status": "ok", "output": result, "kind": "mcp"}


class CustomToolExecutor(ToolExecutor):
    """A generic deterministic executor for the Custom / Knowledge categories (no external I/O)."""

    category = "custom"

    def invoke(self, tool_id, *, arguments=None, config=None) -> dict[str, Any]:
        return {
            "tool_id": tool_id, "status": "ok", "output": f"custom::{tool_id}", "kind": "custom",
        }


class ToolExecutorDispatcher:
    """Picks an executor by tool category and runs it under the tool's timeout + retry policy.
    Respects ordering/enable/config-override/dependencies (already resolved into the plan)."""

    def __init__(self, executors: dict[str, ToolExecutor]) -> None:
        self._executors = executors

    def invoke_step(self, step: dict[str, Any], *, arguments: dict[str, Any]) -> dict[str, Any]:
        category = step.get("category", "custom")
        executor = self._executors.get(category, self._executors["custom"])
        config = dict(step.get("config", {}))
        retry_cfg = config.get("retry", {})
        timeout_ms = int(config.get("timeout_ms", 30_000))
        try:
            return execute_with_resilience(
                lambda: executor.invoke(step["tool_id"], arguments=arguments, config=config),
                timeout_ms=timeout_ms,
                retry=RetrySpec(max_retries=int(retry_cfg.get("max_retries", 0))),
                latency_of=lambda r: int(r.get("latency_ms", 0)),
            )
        except Exception as exc:  # tool failure is captured, not fatal (graceful degradation)
            return {
                "tool_id": step["tool_id"], "status": "error", "output": None,
                "error": str(exc), "category": category,
            }


def default_executors(
    transport: Transport, *, functions: dict[str, Callable[..., Any]] | None = None,
    db_connector=None, mcp_connector=None, knowledge_executor: ToolExecutor | None = None,
) -> dict[str, ToolExecutor]:
    """The default executor set covering HTTP/Function/Webhook/MCP/Database/Custom + Knowledge.
    `knowledge_executor` (M8.15) is the real Knowledge Tool when wired; otherwise the generic
    executor stands in (retrieval unavailable)."""
    http = HttpToolExecutor(transport)
    custom = CustomToolExecutor()
    return {
        "http_api": http,
        "webhook": WebhookToolExecutor(transport),
        "function": FunctionToolExecutor(functions),
        "database": DatabaseToolExecutor(db_connector),
        "mcp": McpToolExecutor(mcp_connector),
        "custom": custom,
        "knowledge": knowledge_executor or custom,  # M8.15 — real retrieval when wired
    }
