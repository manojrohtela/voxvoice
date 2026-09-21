"""The console application's OpenAPI document, with its credentials made explicit (M12.7).

The routes are the source of truth; this only *annotates* what FastAPI generates from them. It is
installed on the console application at composition, so no frozen router or `get_org` changes, and
it describes the three credentials of ADR-0055 DP4 as what they are:

* `developer`: the organization's API key in `x-api-key` (scheme `ApiKeyAuth`);
* `signalling`: the single-purpose token a rendezvous mints, in `x-signalling-token`
  (`SignallingToken`);
* `console`: the console session cookie, plus the CSRF header on mutations (`ConsoleSession`);
* `public`: no credential.

Every operation carries `x-voxvoice-surface`. The developer contract — what the M12.9 SDK is built
from — is exactly the `developer` and `signalling` operations. The credential headers are declared
once, as security schemes, rather than again as per-operation parameters.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

SURFACE = "x-voxvoice-surface"

SECURITY_SCHEMES: dict[str, dict[str, str]] = {
    "ApiKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key",
        "description": (
            "Your organization's developer API key, created by an owner or admin in the console "
            "(API keys). Server-to-server only: never put it in a browser or a mobile app."
        ),
    },
    "SignallingToken": {
        "type": "apiKey",
        "in": "header",
        "name": "x-signalling-token",
        "description": (
            "The single-purpose token returned by POST /v1/webrtc/rendezvous. It can exchange one "
            "offer/answer for one session and nothing else, so it is safe to hand to a browser."
        ),
    },
    "ConsoleSession": {
        "type": "apiKey",
        "in": "cookie",
        "name": "vv_console",
        "description": (
            "The VoxVoice console's sign-in session (first-party only; not a developer "
            "credential). Mutations also require the header `x-voxvoice-console: 1`."
        ),
    },
}

#: Credential header parameters that the schemes above replace.
_CREDENTIAL_HEADERS = {"x-api-key", "x-signalling-token"}

_PUBLIC = {
    ("GET", "/health"),
    ("GET", "/v1/console/meta"),
    ("POST", "/v1/console/auth/signup"),
    ("POST", "/v1/console/auth/login"),
}
_SIGNALLING = re.compile(r"^/v1/webrtc/rendezvous/\{[^}]+\}/(offer|answer)$")
_DEVELOPER = re.compile(r"^/v1/(agents|sessions)(/|$)|^/v1/webrtc/(rendezvous|sessions)$")

TAGS = [
    {
        "name": "agents",
        "description": "Developer API: create and configure voice agents (x-api-key).",
    },
    {
        "name": "sessions",
        "description": "Developer API: start, inspect and end agent sessions (x-api-key).",
    },
    {
        "name": "webrtc",
        "description": (
            "Developer API: connect a caller's browser to a session over WebRTC. Present only "
            "when the server has voice enabled."
        ),
    },
    {
        "name": "console",
        "description": (
            "Console API: first-party, cookie-authenticated. Not part of the developer API."
        ),
    },
]


def surface_of(method: str, path: str) -> str:
    if (method, path) in _PUBLIC:
        return "public"
    if _SIGNALLING.match(path):
        return "signalling"
    if _DEVELOPER.match(path):
        return "developer"
    if path.startswith("/v1/console/"):
        return "console"
    return "internal"


_SECURITY = {
    "developer": [{"ApiKeyAuth": []}],
    "signalling": [{"SignallingToken": []}],
    "console": [{"ConsoleSession": []}],
    "public": [],
    "internal": [],
}


def annotate(schema: dict[str, Any]) -> dict[str, Any]:
    schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    schema["tags"] = TAGS
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            surface = surface_of(method.upper(), path)
            operation[SURFACE] = surface
            operation["security"] = _SECURITY[surface]
            params = [
                p
                for p in operation.get("parameters", [])
                if not (p.get("in") == "header" and p.get("name") in _CREDENTIAL_HEADERS)
            ]
            if params:
                operation["parameters"] = params
            else:
                operation.pop("parameters", None)
    return schema


def install(app: FastAPI) -> None:
    """Serve the annotated document from the app's usual `/openapi.json` (generated once)."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = annotate(
                get_openapi(
                    title="VoxVoice Platform",
                    version=app.version,
                    description=(
                        "The developer API (`x-api-key`) and the first-party console API. Each "
                        f"operation's `{SURFACE}` says which it belongs to."
                    ),
                    routes=app.routes,
                )
            )
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
