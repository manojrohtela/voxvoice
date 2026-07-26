"""bridge — composition-root modules wiring two closed runtimes together without
either importing the other. M5.6B-2 adds `ProviderModelBridge` (provider ⇄ reasoning);
M6.8B-2 adds `MCPToolBridge`/`MCPToolRegistrar` (mcp ⇄ tool). Neither wired runtime
imports the other, nor this package — only a composition root imports `bridge`."""

from .mcp_tool_bridge import MCPToolBridge, MCPToolRegistrar
from .provider_model_bridge import ProviderModelBridge

__all__ = ["ProviderModelBridge", "MCPToolBridge", "MCPToolRegistrar"]
