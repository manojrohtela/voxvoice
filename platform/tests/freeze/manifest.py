"""Epic 9 Freeze Manifest (M9.25) — the certified state, rendered as deterministic evidence.

The roadmap specifies M9.25 exactly (`docs/roadmap/epic9-implementation-plan.md` §10, P6):

> **M9.25 Architecture Freeze** — Billing Platform v1.0 Freeze Manifest: frozen interfaces,
> ownership, dependency direction, extension policy. Output: *a freeze manifest*. Complexity **S**.
> DoD: *architecture frozen; extension policy stated; **commit state recorded honestly***.

M9.25 introduces **no financial fact, no table, no migration, no route, no Studio page and no
production module**. It reads the tree M9.24 certified and writes down what is there.

## Derived versus declared

The manifest has two halves, and the split is the honest part.

**Derived** values are computed from the repository on every run: module counts, table and index
counts, route counts, event and lifecycle vocabularies, namespace sizes, the certification verdict.
Re-running reproduces them exactly.

**Declared** values cannot be derived by importing anything — the repository-wide regression result
and the ruff result are facts about *running* the suite, not about reading the tree. They are
supplied as **release inputs** and recorded as such. A manifest that pretended to derive them would
be claiming a reproducibility it does not have.

Both halves enter the digest. FZ-12's "reconstructable from the manifest and its declared release
inputs" means exactly this pair and nothing more.

## Determinism

Nothing volatile enters: no timestamp, no UUID, no process id, no absolute path, no environment
value, no git working-directory path. The digest reuses **M9.21's canonicalizer** — the single one
in this repository, which certification control `DT-3` asserts is unique. This module declares no
second canonicalizer and no second digest function.
"""

from __future__ import annotations

import pkgutil
from pathlib import Path

from billing.foundation import canonical_digest

#: Bumped only when a change here would produce a different manifest from the same tree.
FREEZE_MANIFEST_VERSION = "m9.25.1"

#: The Epic 9 milestone range this manifest freezes.
EPIC9_FIRST_MILESTONE = "M9.2"
EPIC9_LAST_MILESTONE = "M9.24"

#: The migration head M9.24 certified. Asserted, never inferred: a freeze that read the head from
#: the chain would happily freeze a chain that had grown.
CERTIFIED_MIGRATION_HEAD = "0033"


def _module_count(package) -> int:
    return len(list(pkgutil.iter_modules(package.__path__)))


def derived_inventory() -> dict:
    """Everything the manifest can compute from the tree itself."""
    import billing.application as A
    import billing.application.events as AE
    import billing.customer as C
    import billing.customer.routers as R
    import billing.foundation as F
    import billing.persistence as P
    import billing.persistence.database  # noqa: F401  (registers every Billing table)
    from billing.persistence import migrations
    from tests.certification.catalogue import (
        CERTIFICATION_VERSION,
        CONTROLS_BY_ID,
        DECLARED_LIMITATIONS,
        ControlSeverity,
    )

    # Billing's own tables, resolved by which module declared the row class — **not** the
    # process-wide `SQLModel.metadata`, which in a full test session also holds `api.models`'
    # five tables. Reading the global registry would make the manifest's table and index counts
    # depend on import order, which is precisely the non-determinism a freeze manifest must not
    # have. (M9.25 D-2 — the same defect class M9.24 fixed for its ownership controls.)
    from tests.certification.runner import CertificationEvidence, certify

    tables = CertificationEvidence._billing_tables()
    index_names = [i.name for t in tables.values() for i in t.indexes]

    routes: set[str] = set()
    methods: set[str] = set()
    prefixes: set[str] = set()
    for name in dir(R):
        router = getattr(getattr(R, name), "router", None)
        if router is None:
            continue
        prefix = getattr(router, "prefix", "")
        prefixes.add(prefix)
        for route in router.routes:
            for method in getattr(route, "methods", ()) or ():
                routes.add(f"{method} {prefix}{route.path}")
                methods.add(method)

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    report = certify()
    billing_root = Path(__file__).resolve().parent.parent.parent / "billing"

    return {
        "epic": {
            "first_milestone": EPIC9_FIRST_MILESTONE,
            "last_milestone": EPIC9_LAST_MILESTONE,
            "certified_state": report.verdict.value,
        },
        "architecture": {
            "foundation_modules": _module_count(F),
            "application_modules": _module_count(A),
            "persistence_modules": _module_count(P),
            "customer_modules": _module_count(C),
            "studio_pages": len(list((billing_root / "customer" / "studio").glob("*.html"))),
        },
        "database": {
            "migration_head": versions[-1],
            "migration_first": versions[0],
            "migration_count": len(versions),
            "migration_chain_ordered": versions == sorted(versions),
            "migration_chain_contiguous": [int(v) for v in versions]
            == list(range(int(versions[0]), int(versions[0]) + len(versions))),
            "table_count": len(tables),
            "index_count": len(index_names),
            "index_names_unique": len(set(index_names)) == len(index_names),
            "tables_all_billing_scoped": all(n.startswith("cp_billing_") for n in tables),
        },
        "api": {
            "route_count": len(routes),
            "prefix_count": len(prefixes),
            "methods": sorted(methods),
            "duplicate_routes": 0 if len(routes) == len(set(routes)) else len(routes),
        },
        "events": {
            "billing_domain_events": len(list(F.BillingEventType)),
            "application_events": len(list(AE.ApplicationEventType)),
            "all_billing_namespaced": all(
                e.value.startswith("billing.") for e in F.BillingEventType
            ),
        },
        "lifecycles": {
            "count": len([n for n in F.__all__ if n.endswith("_LIFECYCLE")]),
            "all_have_terminal_states": all(
                bool(getattr(F, n).terminal_states())
                for n in F.__all__
                if n.endswith("_LIFECYCLE")
            ),
        },
        "namespace": {
            "foundation_exports": len(F.__all__),
            "application_exports": len(A.__all__),
        },
        "certification": {
            "catalogue_version": CERTIFICATION_VERSION,
            "digest": report.report_digest,
            "control_count": len(CONTROLS_BY_ID),
            "blocking_controls": sum(
                1 for c in CONTROLS_BY_ID.values() if c.severity is ControlSeverity.BLOCKING
            ),
            "advisory_controls": sum(
                1 for c in CONTROLS_BY_ID.values() if c.severity is ControlSeverity.ADVISORY
            ),
            "blocking_violations": len(report.blocking_violations),
            "inconclusive": len(report.inconclusive),
            "advisories": sorted(a.control_id for a in report.advisories),
            "results": {r.control_id: r.outcome.value for r in report.results},
            "limitations": [
                {"id": limit.id, "raised_by": limit.milestone, "owner": limit.owner}
                for limit in sorted(DECLARED_LIMITATIONS, key=lambda x: x.id)
            ],
        },
    }


def build_manifest(
    *,
    repository_wide_tests: int,
    failed: int,
    skipped: int,
    ruff_clean: bool,
    commit_state: str,
) -> dict:
    """The full manifest: derived inventory plus the declared release inputs.

    `repository_wide_tests`, `failed`, `skipped` and `ruff_clean` are **declared**, not derived —
    they are facts about running the suite, and a manifest that claimed to compute them by reading
    the tree would be overstating what it can prove.

    `commit_state` records the repository's VCS posture honestly, which is M9.25's stated DoD line.
    It is a classification (`"uncommitted"`, `"committed"`), never a path or a working-directory
    value, so the manifest stays machine-independent."""
    return {
        "manifest_version": FREEZE_MANIFEST_VERSION,
        "freeze_status": "frozen",
        "declared_release_inputs": {
            "repository_wide_tests": repository_wide_tests,
            "failed": failed,
            "skipped": skipped,
            "ruff_clean": ruff_clean,
            "commit_state": commit_state,
        },
        "derived": derived_inventory(),
    }


def manifest_digest(manifest: dict) -> str:
    """Content digest over the whole manifest, using **M9.21's canonicalizer**.

    No second canonicalizer is declared here — `DT-3` certifies that exactly one exists in this
    repository, and this module calls it rather than reimplementing it."""
    return canonical_digest(manifest)


def render(manifest: dict) -> str:
    """The manifest as stable text. Deterministic: sorted keys, no clock, no path, no host."""
    lines: list[str] = []

    def walk(node: object, prefix: str = "") -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], f"{prefix}{key}.")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{prefix}{index}.")
        else:
            lines.append(f"{prefix.rstrip('.')}={node}")

    walk(manifest)
    lines.append(f"manifest_digest={manifest_digest(manifest)}")
    return "\n".join(lines)


__all__ = [
    "CERTIFIED_MIGRATION_HEAD",
    "EPIC9_FIRST_MILESTONE",
    "EPIC9_LAST_MILESTONE",
    "FREEZE_MANIFEST_VERSION",
    "build_manifest",
    "derived_inventory",
    "manifest_digest",
    "render",
]
