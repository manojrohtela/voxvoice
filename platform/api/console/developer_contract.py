"""The developer API contract, as a file the SDK is tested against (M12.9).

The server's OpenAPI document is the one source of truth for the developer API (ADR-0055 DP13).
This extracts from it exactly the `developer` and `signalling` operations and the schemas they
reference, into `sdk/contract/developer-openapi.json`. The SDK's contract tests read that file; a
platform test fails whenever it differs from what the server generates, so neither side can drift
without the other noticing. Deterministic: sorted keys, no timestamps, no credentials.

Regenerate from `platform/`:

    python -m api.console.developer_contract > ../sdk/contract/developer-openapi.json
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .openapi import SURFACE

#: The surfaces an SDK may call. Console and public operations are not part of it.
DEVELOPER_SURFACES = ("developer", "signalling")


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"].rsplit("/", 1)[-1]} if isinstance(node.get("$ref"), str) else set()
        for value in node.values():
            found |= _refs(value)
        return found
    if isinstance(node, list):
        found: set[str] = set()
        for value in node:
            found |= _refs(value)
        return found
    return set()


def extract(schema: dict[str, Any]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for path, operations in schema["paths"].items():
        kept = {m: op for m, op in operations.items() if op.get(SURFACE) in DEVELOPER_SURFACES}
        if kept:
            paths[path] = kept
    all_schemas = schema.get("components", {}).get("schemas", {})
    wanted, pending = set(), _refs(paths)
    while pending:
        name = pending.pop()
        if name in wanted:
            continue
        wanted.add(name)
        pending |= _refs(all_schemas.get(name, {}))
    schemes = schema["components"]["securitySchemes"]
    return {
        "openapi": schema["openapi"],
        "info": {"title": "VoxVoice developer API", "version": "v1"},
        "paths": paths,
        "components": {
            "schemas": {name: all_schemas[name] for name in sorted(wanted)},
            "securitySchemes": {k: schemes[k] for k in ("ApiKeyAuth", "SignallingToken")},
        },
    }


def generate() -> dict[str, Any]:
    """The contract of a server with every developer surface mounted (signalling included)."""
    from ..webrtc.config import WebRTCSettings
    from .app import build_console_app
    from .config import ConsoleSettings

    app = build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings(signalling=True))
    return extract(app.openapi())


def render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":  # pragma: no cover — a developer command
    sys.stdout.write(render(generate()))
