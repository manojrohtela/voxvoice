"""M12.11 — the Epic 12 certification control set, executable (`E12-1 … E12-12`).

The repository's standard for a certification is that it is **reproducible as tests, not a one-off
document** (M9.24's Definition of Done, `tests/certification/catalogue.py`), and that a control
whose evidence cannot be gathered fails rather than passes (ADR-0051 **CE3**). So the structural
claims in [`docs/testing/m12-certification.md`](../../docs/testing/m12-certification.md) are made
here, against the current tree, and the record cites this module.

The namespace `E12-*` is fresh and disjoint from Epic 9's, Epic 10's and Epic 11's (the CE1 rule).
Controls that are *behavioural* (a call connects, a key is refused after revocation) are not
re-implemented here: they are owned by the suites this module points at, and the record names them.
What is proven here is what a document cannot prove about itself: boundaries, shapes and honesty.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.console import ConsoleSettings, build_console_app
from api.console.developer_contract import generate, render
from api.console.openapi import SURFACE
from api.console.routers.meta import CAPABILITIES
from api.webrtc.config import WebRTCSettings

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "platform"
CONSOLE = REPO / "api" / "console"  # relative to PLATFORM, joined below
RECORD = REPO / "docs" / "testing" / "m12-certification.md"

#: The commit M12 started from: `M12.1` is the first Epic 12 commit, so its parent is the baseline
#: every "what did M12 change?" question is asked against.
M12_BASE = "1a03d90^"
#: The **last** Epic 12 commit (M12.14). `E12-1` and `E12-2` ask what Epic 12 *did*, so their range
#: is pinned at both ends. Deriving the near end from `HEAD` asks *"has anyone changed this, ever,
#: in any later epic?"* — the different question ADR-0052 Amendment 2 corrected for `E10-7` at
#: M12.1.1, and which `tests/freeze/epic10.py` states as doctrine: *"the frozen boundary is a
#: commit range, not `HEAD` and not the working tree."* M11.18 — authorized to edit Epic 11
#: provider code — made both `E12-1` controls fail on changes Epic 12 never made.
#:
#: **Epic 12's certification range is CLOSED at this commit.** It is never bumped automatically: a
#: future M12 milestone extends it only through an explicit, reviewed ADR-0055 amendment that also
#: renews verification of `AUTHORIZED_EDITS` (ADR-0055 Amendment 6, DP16).
M12_HEAD = "76acdbb"
#: The frozen Epic 10 baseline: `E10-7` freezes every `platform/api/**` file that existed at it.
FROZEN_BASELINE = "v4.0.0"

#: Pre-existing platform files M12 is **authorized** to have modified, and why.
AUTHORIZED_EDITS = {
    "platform/.env.example": "documents VOXVOICE_CONSOLE_* configuration (M12.2, M12.5)",
    "platform/api/webrtc/app.py": "M11 file created after v4.0.0: observability passthrough + "
    "transcript `ts` (ADR-0055 Amendment 1, F-2/F-3)",
    "platform/bootstrap.py": "M11 file created after v4.0.0: `observability` passthrough "
    "(ADR-0055 Amendment 1)",
    "platform/tests/certification/epic10.py": "E10-7 historical counting repair (ADR-0052 "
    "Amendment 2, M12.1.1)",
    "platform/tests/test_epic10_historical_counting.py": "tests for that repair (M12.1.1)",
}

#: Runtime and spine trees M12 must not have touched at all.
UNTOUCHABLE = (
    "platform/events",
    "platform/observability",
    "platform/runtime",
    "platform/core",
    "platform/providers",
    "platform/billing",
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()


def _changed(base: str, *paths: str, until: str = "HEAD") -> dict[str, str]:
    """`{path: status}` for `base..until`.

    `until` defaults to `HEAD` because one caller is deliberately open-ended: `E10-7` freezes every
    pre-`v4.0.0` `platform/api` file **permanently**, so a modification today is a real violation
    and must fail. Callers asking what one epic *did* pass `until=M12_HEAD` instead."""
    out = _git("diff", "--name-status", base, until, "--", *paths)
    return {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in out.splitlines() if line}


def _console_files() -> list[Path]:
    return sorted((PLATFORM / "api" / "console").rglob("*.py"))


# ══ E12-1 — the frozen platform is untouched ══════════════════════════════════


def test_e12_1_no_file_frozen_at_v4_0_0_was_modified():
    """`E10-7`: every `platform/api/**` file that existed at `v4.0.0` is frozen. New files are the
    sanctioned way to add (ADR-0043 CD7), and Epic 12 is built entirely that way."""
    modified = [p for p, s in _changed(FROZEN_BASELINE, "platform/api").items() if s != "A"]
    assert modified == [], f"frozen api files modified: {modified}"


def test_e12_1_m12_changed_no_runtime_or_spine_module():
    """Epic 10's spine and Epic 11's runtime are not Epic 12's to change (ADR-0055 DP2/DP6)."""
    assert _changed(M12_BASE, *UNTOUCHABLE, until=M12_HEAD) == {}


def test_e12_1_every_pre_existing_file_m12_modified_is_authorized():
    """M12 may edit a post-`v4.0.0` composition file, but only the ones the ADRs name."""
    modified = {p for p, s in _changed(M12_BASE, "platform", until=M12_HEAD).items() if s != "A"}
    assert modified == set(AUTHORIZED_EDITS), (
        f"unauthorized: {sorted(modified - set(AUTHORIZED_EDITS))}; "
        f"expected but absent: {sorted(set(AUTHORIZED_EDITS) - modified)}"
    )


# ══ E12-2 — the product surface is additive and correctly named ═══════════════


def test_e12_2_the_console_is_one_additive_package_with_no_forbidden_path():
    added = {p for p, s in _changed(M12_BASE, "platform/api", until=M12_HEAD).items() if s == "A"}
    assert added, "no Epic 12 api files found"
    assert all(p.startswith("platform/api/console/") for p in added), sorted(added)
    # E10-9: no path under `api/` may contain "observability", new files included.
    assert not [p for p in added if "observability" in p.lower()]


def test_e12_2_the_console_reaches_the_runtime_only_through_the_composition_root():
    """AR-14: `api` never statically depends on `runtime`. The console composes through
    `bootstrap`, in deferred imports, and nowhere else."""
    offenders = []
    for path in _console_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = (
                node.module
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else next((a.name for a in node.names), None)
                if isinstance(node, ast.Import)
                else None
            )
            if module and module.split(".")[0] in {"runtime", "providers"}:
                offenders.append(f"{path.name}: {module}")
    assert offenders == [], offenders


# ══ E12-3 — provider neutrality ═══════════════════════════════════════════════


VENDORS = re.compile(r"groq|deepgram|cartesia|silero|elevenlabs|openai", re.IGNORECASE)


def test_e12_3_no_vendor_identifier_in_the_product_surface():
    """`E11-3`: the product layer names no vendor. The runtime chooses providers."""
    trees = [PLATFORM / "api" / "console", REPO / "sdk" / "src", REPO / "console" / "src"]
    offenders = []
    for tree in trees:
        for path in [*tree.rglob("*.py"), *tree.rglob("*.ts"), *tree.rglob("*.tsx")]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if VENDORS.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == [], offenders


# ══ E12-4 — the read surfaces are read-only ═══════════════════════════════════


@pytest.mark.parametrize("router", ["conversations", "analytics", "overview"])
def test_e12_4_projection_routers_declare_only_get_routes(router):
    """DP6/DP12: Conversations, Analytics and the Overview project existing data. A write route
    here would make one of them a source of truth."""
    path = PLATFORM / "api" / "console" / "routers" / f"{router}.py"
    source = path.read_text(encoding="utf-8")
    methods = set(re.findall(r"@router\.(get|post|put|patch|delete)", source))
    assert methods == {"get"}, methods


# ══ E12-5 — the three credentials, as the document states them ════════════════


def _spec() -> dict:
    with TestClient(
        build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings(signalling=True))
    ) as client:
        return client.get("/openapi.json").json()


def test_e12_5_every_operation_declares_exactly_one_credential_class():
    expected = {
        "developer": [{"ApiKeyAuth": []}],
        "signalling": [{"SignallingToken": []}],
        "console": [{"ConsoleSession": []}],
        "public": [],
    }
    operations = [
        (method.upper(), path, op)
        for path, ops in _spec()["paths"].items()
        for method, op in ops.items()
    ]
    assert operations
    for method, path, op in operations:
        surface = op[SURFACE]
        assert surface in expected, (method, path, surface)
        assert op["security"] == expected[surface], (method, path)


# ══ E12-6 — the contract is the server's, everywhere ══════════════════════════


def test_e12_6_the_sdk_contract_snapshot_is_the_servers_own_document():
    snapshot = (REPO / "sdk" / "contract" / "developer-openapi.json").read_text(encoding="utf-8")
    assert snapshot == render(generate())


def test_e12_6_the_contract_document_lists_exactly_the_developer_operations():
    contract = json.loads((REPO / "sdk" / "contract" / "developer-openapi.json").read_text("utf-8"))
    documented = set(
        re.findall(
            r"\|\s*`(GET|POST|PATCH|DELETE)`\s*\|\s*`(/v1/[^`]+)`",
            (REPO / "docs" / "apis" / "developer-api.md").read_text(encoding="utf-8"),
        )
    )
    served = {(method.upper(), path) for path, ops in contract["paths"].items() for method in ops}
    assert documented == served


# ══ E12-7 — the console claims only what it implements ════════════════════════


def test_e12_7_declared_capabilities_match_the_mounted_console_surfaces():
    paths = set(_spec()["paths"])
    surface_of = {
        "identity": "/v1/console/me",
        "agents": "/v1/console/agents",
        "playground": "/v1/console/playground/rendezvous",
        "conversations": "/v1/console/conversations",
        "analytics": "/v1/console/analytics/summary",
        "api_keys": "/v1/console/api-keys",
        "overview": "/v1/console/overview",
    }
    for capability, path in surface_of.items():
        assert capability in CAPABILITIES, capability
        assert path in paths, path
    # `runtime.status` is the meta/health surface every build has.
    assert set(CAPABILITIES) == {"runtime.status", *surface_of}


# ══ E12-8 — no test was weakened to certify ═══════════════════════════════════


M12_TESTS = ("test_console_*.py", "test_m12_*.py", "test_developer_contract.py")


def test_e12_8_no_m12_test_is_skipped_expected_to_fail_or_conditional():
    offenders = []
    for pattern in M12_TESTS:
        for path in sorted((PLATFORM / "tests").glob(pattern)):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(", line):
                    offenders.append(f"{path.name}:{number}")
    assert offenders == [], offenders


def test_e12_8_the_m12_suites_are_all_present_and_populated():
    """Evidence exists for each milestone that owns a suite: a deleted suite is a failed control."""
    expected = {
        "test_console_foundation.py": 1,  # M12.1
        "test_epic10_historical_counting.py": 1,  # M12.1.1
        "test_console_identity.py": 1,  # M12.2
        "test_console_agents.py": 1,  # M12.3
        "test_console_playground.py": 1,  # M12.4
        "test_console_conversations.py": 1,  # M12.5
        "test_console_analytics.py": 1,  # M12.6
        "test_console_api_keys.py": 1,  # M12.7
        "test_console_overview.py": 1,  # M12.8
        "test_developer_contract.py": 1,  # M12.9
        "test_m12_e2e.py": 7,  # M12.10 (7 functions; the role matrix parametrizes to 8 cases)
    }
    for name, minimum in expected.items():
        source = (PLATFORM / "tests" / name).read_text(encoding="utf-8")
        found = len(re.findall(r"^(?:async )?def test_", source, re.MULTILINE))
        assert found >= minimum, f"{name}: {found} tests"


# ══ E12-9 — no credential is committed ════════════════════════════════════════


SECRETS = re.compile(
    r"vf_[A-Za-z0-9_-]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def test_e12_9_no_committed_file_of_epic_12_contains_a_credential():
    tracked = _git(
        "ls-files", "platform/api/console", "sdk", "console/src", "e2e", "docs"
    ).splitlines()
    offenders = []
    for name in tracked:
        path = REPO / name
        if path.suffix in {".png", ".ico"} or not path.is_file():
            continue
        if SECRETS.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(name)
    assert offenders == [], offenders


def test_e12_9_no_build_output_or_dependency_tree_is_tracked():
    tracked = _git("ls-files").splitlines()
    offenders = [
        n
        for n in tracked
        if "/node_modules/" in n or re.search(r"(^|/)(dist|e2e-out[^/]*)/", n) or n.endswith(".db")
    ]
    assert offenders == [], offenders


# ══ E12-10 — the record itself ════════════════════════════════════════════════


def test_e12_10_the_certification_record_exists_and_carries_a_verdict_per_control():
    record = RECORD.read_text(encoding="utf-8")
    for milestone in (
        "M12.1", "M12.1.1", "M12.2", "M12.3", "M12.4", "M12.5",
        "M12.6", "M12.7", "M12.8", "M12.9", "M12.10",
    ):  # fmt: skip
        assert milestone in record, milestone
    controls = set(re.findall(r"`(E12-\d+)`", record))
    implemented = {
        f"E12-{n}"
        for n in re.findall(r"def test_e12_(\d+)_", Path(__file__).read_text(encoding="utf-8"))
    }
    assert implemented <= controls, f"controls not in the record: {sorted(implemented - controls)}"
    # Every stated limitation is classified, and the verdict is one of the three permitted ones.
    assert "NOT EXECUTED" in record
    assert re.search(r"real[- ](provider|speech)", record, re.IGNORECASE)
    assert re.search(
        r"CERTIFIED WITH DOCUMENTED NON-BLOCKING RISKS|NOT CERTIFIED|^CERTIFIED", record, re.M
    )


def test_e12_10_every_epic_12_document_is_linked_from_the_index():
    index = (REPO / "docs" / "INDEX.md").read_text(encoding="utf-8")
    documents = [
        "apis/developer-api.md",
        "apis/m12-api-design.md",
        "architecture/epic12/m12-analytics-definitions.md",
        "architecture/epic12/m12-lld.md",
        "security/m12-security-model.md",
        "testing/m12-e2e.md",
        "testing/m12-certification.md",
        "../sdk/README.md",
        "../e2e/README.md",
    ]
    missing = [d for d in documents if d not in index]
    assert missing == [], missing
