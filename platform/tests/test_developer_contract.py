"""M12.9 — the SDK's contract file is the server's developer OpenAPI, exactly (ADR-0055 DP13).

`sdk/contract/developer-openapi.json` is what the SDK's contract tests hold every method and type
to. This fails the moment it differs from what the server generates, so the SDK cannot drift from
the API (or the API from the SDK) unnoticed. Regenerate with
`python -m api.console.developer_contract > ../sdk/contract/developer-openapi.json`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from api.console.developer_contract import DEVELOPER_SURFACES, extract, generate, render
from api.console.openapi import SURFACE

REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "sdk" / "contract" / "developer-openapi.json"
SDK_TEXT = [REPO / "sdk" / "README.md", *sorted((REPO / "sdk" / "src").rglob("*.ts"))]


def test_the_committed_contract_is_what_the_server_generates():
    assert CONTRACT.read_text(encoding="utf-8") == render(generate()), (
        "sdk/contract/developer-openapi.json is stale: regenerate it (see this module's docstring)"
    )


def test_the_contract_holds_only_developer_operations_and_their_schemas():
    contract = generate()
    operations = [op for ops in contract["paths"].values() for op in ops.values()]
    assert len(operations) == 13
    assert {op[SURFACE] for op in operations} == set(DEVELOPER_SURFACES)
    assert not any(path.startswith("/v1/console") for path in contract["paths"])
    assert set(contract["components"]["securitySchemes"]) == {"ApiKeyAuth", "SignallingToken"}
    # Every referenced schema is included, and nothing unreferenced is.
    text = json.dumps(contract["paths"])
    referenced = set(re.findall(r"#/components/schemas/([A-Za-z0-9_]+)", text))
    assert referenced <= set(contract["components"]["schemas"])
    assert extract(contract | {"components": {**contract["components"]}}) == contract


def test_the_sdk_sources_and_readme_hold_no_credentials():
    for path in SDK_TEXT:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"vf_[A-Za-z0-9_-]{20,}", text), path
    readme = (REPO / "sdk" / "README.md").read_text(encoding="utf-8")
    assert "process.env.VOXVOICE_API_KEY" in readme
