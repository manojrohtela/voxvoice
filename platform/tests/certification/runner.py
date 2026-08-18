"""Billing Certification runner (M9.24) — evidence gathering and control evaluation.

The Foundation declares *what* is certified and *how a verdict follows*. This module gathers the
evidence and runs the controls, because reading a module graph, a schema and a route table is I/O
and Foundation does none.

## What this module is not

It is not a domain engine. There are **no commands, no queries, no repositories, no Unit of Work
and no command bus** here — and there is nothing for them to write, because M9.24 owns no financial
fact. It reads the repository and returns a value.

It dispatches nothing. It persists nothing. It has no organization scope, because *"does this
repository satisfy its architecture?"* is not an organization's question — which is exactly why
M9.24 ships **no customer API surface**: an org-scoped endpoint serving a repository-wide
engineering fact would be a lie about ownership, in a milestone whose whole job is to catch those.

## How a control fails

Every evaluator returns `SATISFIED`, `VIOLATED` (naming what offended), `NOT_APPLICABLE`, or
`INCONCLUSIVE` (saying why it could not look). An evaluator that raises is caught and converted to
`INCONCLUSIVE` — never to a pass. A certification suite that swallowed its own errors as successes
would certify its blind spots, which is the one failure mode that makes certification worse than
nothing.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from collections.abc import Iterable
from pathlib import Path

from tests.certification.catalogue import (
    CERTIFICATION_CONTROLS,
    CertificationReport,
    CertificationService,
    ControlOutcome,
    ControlResult,
)

#: The Billing package root on disk. Resolved from this module rather than from the working
#: directory, so certification produces the same answer wherever it is invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BILLING_ROOT = REPO_ROOT / "billing"

#: Planes Billing may never import (ADR-0031 IR1/IR3). `core` is the one shared downward dependency.
FORBIDDEN_PLANES = ("runtime", "actuator", "api", "product", "bootstrap", "providers")

#: Vendor SDK roots that may appear only behind an adapter boundary (ADR-0031 PS2).
VENDOR_ROOTS = ("stripe", "razorpay", "paddle", "braintree", "adyen", "avalara", "taxjar", "boto3")

#: Payment-instrument and credential vocabulary that must not reach a model, DTO, event or page.
INSTRUMENT_TOKENS = (
    "card_number", "cardnumber", "cvv", "cvc", "iban", "routing_number", "sort_code",
    "pan_number", "secret_key", "private_key", "api_key", "apikey", "access_token",
    "refresh_token", "bearer_token", "password",
)

#: Forbidden-Write concepts (ADR-0023 §7) that must never appear as a Billing table name.
#: Whole concepts, not substrings. `member` alone would fire on `cp_billing_scope_member` — an
#: account inside a consolidation scope, which is M9.23's own fact and not a Customer Platform
#: membership. A control that cannot tell those apart teaches people to ignore it.
FORBIDDEN_TABLE_TOKENS = (
    "organization", "workspace", "project", "org_member", "team_member", "invitation",
    "agent", "prompt_", "skill", "workflow", "knowledge", "document_chunk",
    "deployment", "session", "turn_", "conversation", "audit_record",
    "metric_event", "rbac_", "permission_grant",
)


def _py_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _code_only(source: str) -> str:
    """Source with docstrings and comments removed.

    Certification must not fire on prose that *names* a forbidden thing in order to forbid it —
    every module in this plane documents what it refuses to do, and a scan that could not tell a
    prohibition from a violation would be unusable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a syntactically broken file fails elsewhere
        return source
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                node.value.value = ""
    return ast.unparse(tree)


def _billing_code() -> dict[Path, str]:
    """Every Billing Python file, docstring- and comment-free. Computed once per run.

    The scanner needs no exemption from its own token lists: it lives in `tests/certification/`,
    outside `BILLING_ROOT`, so it is **structurally** outside the corpus it inspects rather than
    excluded from it by filename. An earlier draft lived in `billing/application/` and needed a
    name-based exclusion; the relocation that fixed nine architecture-test failures removed the
    need for it entirely.""" 
    return {p: _code_only(p.read_text()) for p in _py_files(BILLING_ROOT)}


def _billing_model_modules():
    """Every `billing.persistence.*_models` module, imported."""
    import billing.persistence as persistence

    out = []
    for info in pkgutil.iter_modules(persistence.__path__):
        if info.name.endswith("models") or info.name == "models":
            # Deliberately unguarded. A model module that cannot be imported means the ownership
            # controls cannot see part of the schema, and `certify()` turns the resulting
            # exception into INCONCLUSIVE — which fails certification. Swallowing it here would
            # shrink coverage silently and still report SATISFIED.
            out.append(importlib.import_module(f"billing.persistence.{info.name}"))
    return out


def _imported_modules(source: str) -> set[str]:
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


# ── evidence ────────────────────────────────────────────────────────────────────


class CertificationEvidence:
    """Everything the controls read, gathered once.

    Held as a single object so a control cannot quietly widen its own evidence: what is available
    to inspect is decided here, in one place, rather than by whatever each evaluator felt like
    importing."""

    def __init__(self) -> None:
        self.code = _billing_code()
        self.tables = self._billing_tables()
        self.all_tables = self._all_tables()
        self.lifecycles = self._lifecycles()

    @staticmethod
    def _billing_tables():
        """**Billing's own tables, not the process-wide registry.**

        `SQLModel.metadata` is global: in a full test session every plane's models have registered
        into it, so a control reading it directly would report another epic's `agent` table as a
        Billing ownership violation. Ownership is decided by which module declared the row class,
        which is the only definition of "Billing writes this" that means anything."""
        import billing.persistence.database  # noqa: F401  (registers every Billing table)

        owned = {}
        for module in _billing_model_modules():
            for value in vars(module).values():
                table = getattr(value, "__table__", None)
                name = getattr(value, "__tablename__", None)
                if table is not None and isinstance(name, str):
                    owned[name] = table
        return owned

    @staticmethod
    def _all_tables():
        """Every table registered in this process, whoever owns it — what NS-2 checks against, so
        a Billing index cannot collide with another plane's either."""
        from sqlmodel import SQLModel

        import billing.persistence.database  # noqa: F401

        return dict(SQLModel.metadata.tables)

    @staticmethod
    def _lifecycles():
        import billing.foundation as F

        return {n: getattr(F, n) for n in F.__all__ if n.endswith("_LIFECYCLE")}

    def foundation_code(self) -> dict[Path, str]:
        return {p: s for p, s in self.code.items() if "foundation" in p.parts}

    def studio_pages(self) -> dict[Path, str]:
        studio = BILLING_ROOT / "customer" / "studio"
        return {p: p.read_text() for p in sorted(studio.glob("*.html"))}


# ── control evaluators ──────────────────────────────────────────────────────────


def _ok(control_id: str, examined: int, detail: str = "") -> ControlResult:
    return ControlResult(control_id, ControlOutcome.SATISFIED, detail, (), examined)


def _bad(
    control_id: str, violations: Iterable[str], examined: int, detail: str = ""
) -> ControlResult:
    found = tuple(sorted(set(violations)))
    if not found:
        return _ok(control_id, examined, detail)
    return ControlResult(control_id, ControlOutcome.VIOLATED, detail, found, examined)


def _own_1(e: CertificationEvidence) -> ControlResult:
    bad = [n for n in e.tables if not n.startswith("cp_billing_")]
    return _bad("OWN-1", bad, len(e.tables), f"{len(e.tables)} tables registered by Billing")


def _own_2(e: CertificationEvidence) -> ControlResult:
    bad = [
        f"{name} (matches {token!r})"
        for name in e.tables
        for token in FORBIDDEN_TABLE_TOKENS
        if token in name
    ]
    return _bad("OWN-2", bad, len(e.tables))


def _own_3(e: CertificationEvidence) -> ControlResult:
    """Billing must not *produce* on the `metric.*` stream.

    Checks the emitted event vocabulary, not the source text: several modules quote a `metric.*`
    type in an OpenAPI example precisely to document what Billing **consumes**, and a scan that
    could not tell a consumed example from a produced event would fire on the documentation."""
    import billing.foundation as F

    bad = [v.value for v in F.BillingEventType if v.value.startswith("metric.")]
    return _bad(
        "OWN-3", bad, len(list(F.BillingEventType)),
        "Billing consumes metric.* and never produces on it",
    )


def _own_4(e: CertificationEvidence) -> ControlResult:
    """ADR-0023 §6.18 governs Billing's **domain** events on the §A5 envelope.

    `ApplicationEventType` is a separate, internal use-case vocabulary that never reaches the
    platform envelope, so it is deliberately out of scope here rather than silently renamed."""
    import billing.foundation as F

    bad = [v.value for v in F.BillingEventType if not v.value.startswith("billing.")]
    return _bad("OWN-4", bad, len(list(F.BillingEventType)))


#: Repositories holding **revenue history**, which RS1/RS3 make append-only. Declared by name
#: rather than inferred, because the rule is about revenue records — not about every repository.
#: `PaymentInstrumentRepository.remove` is deliberately absent: detaching a stored provider token
#: is a customer right that touches no revenue record (PY6), and a control that forbade it would
#: be asserting something the ADR does not say.
APPEND_ONLY_REPOSITORIES = (
    "LedgerEntryRepository", "JournalEntryRepository", "InvoiceRepository",
    "CommercialInvoiceRepository", "CreditNoteRepository", "RefundRepository",
    "TaxAssessmentRepository", "RevenueEntryRepository", "IntegrityFindingRepository",
    "IncidentEventRepository", "EvidenceCaptureRepository", "InvestigationEventRepository",
    "ReportEventRepository", "ReportSnapshotRepository", "ConsolidationEventRepository",
    "ConsolidationSnapshotRepository", "SourceBindingRepository", "ScopeSnapshotRepository",
)


def _rs_1(e: CertificationEvidence) -> ControlResult:
    """No append-only revenue repository declares an edit or delete path."""
    import billing.foundation as F

    bad = []
    checked = 0
    for name in APPEND_ONLY_REPOSITORIES:
        obj = getattr(F, name, None)
        if not isinstance(obj, type):
            continue
        checked += 1
        for method in ("delete", "remove", "purge", "drop", "erase", "update", "edit"):
            if hasattr(obj, method):
                bad.append(f"{name}.{method}")
    if not checked:
        return ControlResult(
            "RS-1", ControlOutcome.INCONCLUSIVE,
            "none of the declared append-only repositories were importable",
        )
    return _bad("RS-1", bad, checked, f"{checked} append-only repository contracts inspected")


def _rs_5(e: CertificationEvidence) -> ControlResult:
    """Behavioural, not a scan: `Money` refuses a float at construction."""
    from billing.foundation import Currency, InvalidMoney, Money

    try:
        Money(1.5, Currency.INR)  # type: ignore[arg-type]
    except InvalidMoney:
        return _ok("RS-5", 1, "Money(float) raises InvalidMoney")
    return ControlResult(
        "RS-5", ControlOutcome.VIOLATED, "Money accepted a float", ("Money.__post_init__",), 1
    )


def _rs_6(e: CertificationEvidence) -> ControlResult:
    tokens = ("exchange_rate", "fx_rate", "conversion_rate", "convert_currency", "forex")
    bad = [
        f"{p.relative_to(REPO_ROOT)}:{t}"
        for p, s in e.code.items()
        for t in tokens
        if t in s
    ]
    return _bad("RS-6", bad, len(e.code), "no FX rate at any altitude")


def _py_5(e: CertificationEvidence) -> ControlResult:
    """No lifecycle offers an automatic resolution trigger."""
    bad = []
    for name, machine in e.lifecycles.items():
        for state in machine.states:
            for trigger in machine.triggers_from(state):
                value = getattr(trigger, "value", str(trigger))
                if value.startswith("auto") or value in ("auto_resolve", "auto_close"):
                    bad.append(f"{name}:{state}--{value}")
    return _bad("PY-5", bad, len(e.lifecycles), f"{len(e.lifecycles)} lifecycles inspected")


def _py_6(e: CertificationEvidence) -> ControlResult:
    surfaces = {
        p: s
        for p, s in e.code.items()
        if any(k in p.name for k in ("_models.py", "_schemas.py", "events.py"))
    }
    bad = [
        f"{p.relative_to(REPO_ROOT)}:{t}"
        for p, s in surfaces.items()
        for t in INSTRUMENT_TOKENS
        if t in s.lower()
    ]
    for p, html in e.studio_pages().items():
        bad.extend(
            f"{p.relative_to(REPO_ROOT)}:{t}" for t in INSTRUMENT_TOKENS if t in html.lower()
        )
    return _bad("PY-6", bad, len(surfaces), "instrument data never enters the platform")


def _tx_1(e: CertificationEvidence) -> ControlResult:
    tokens = ("RATE_TABLE", "TAX_RATES", "RATES_BY_", "JURISDICTION_RATES", "DEFAULT_TAX_RATE")
    bad = [
        f"{p.relative_to(REPO_ROOT)}:{t}"
        for p, s in e.code.items()
        for t in tokens
        if t in s
    ]
    return _bad("TX-1", bad, len(e.code), "no rate table ships with Billing")


def _pl_1(e: CertificationEvidence) -> ControlResult:
    bad = []
    for p, s in e.code.items():
        for module in _imported_modules(s) & set(FORBIDDEN_PLANES):
            bad.append(f"{p.relative_to(REPO_ROOT)} imports {module}")
    return _bad("PL-1", bad, len(e.code), "Billing imports core and stdlib only")


def _pl_2(e: CertificationEvidence) -> ControlResult:
    """No package outside Billing imports Billing."""
    bad = []
    checked = 0
    for plane in FORBIDDEN_PLANES:
        root = REPO_ROOT / plane
        if not root.is_dir():
            continue
        for path in _py_files(root):
            checked += 1
            if "billing" in _imported_modules(path.read_text()):
                bad.append(str(path.relative_to(REPO_ROOT)))
    if not checked:
        return ControlResult(
            "PL-2",
            ControlOutcome.INCONCLUSIVE,
            "no sibling plane packages were found on disk to inspect",
            (),
            0,
        )
    return _bad("PL-2", bad, checked)


#: The one Foundation module permitted to name a clock: it *defines* the seam every other module
#: takes by injection. Purity means domain rules do not read a clock, not that the seam declaring
#: `Clock` cannot mention one.
CLOCK_SEAM = "seams.py"


def _pl_3(e: CertificationEvidence) -> ControlResult:
    """Foundation purity, checked by import rather than by substring.

    An earlier draft of this control matched the text `requests.` and fired on a local variable
    named `requests` holding repair requests — a false positive that would have taught a reader to
    ignore the control. Network access is an *import*, so that is what is checked."""
    network = {"requests", "httpx", "socket", "urllib", "aiohttp"}
    clock_tokens = ("datetime.now", "datetime.utcnow", "time.time(", "random.", "uuid4")
    foundation = e.foundation_code()
    bad = []
    for p, source in foundation.items():
        for module in _imported_modules(source) & network:
            bad.append(f"{p.relative_to(REPO_ROOT)} imports {module}")
        if p.name == CLOCK_SEAM:
            continue
        bad.extend(f"{p.relative_to(REPO_ROOT)}:{t}" for t in clock_tokens if t in source)
    return _bad(
        "PL-3", bad, len(foundation),
        f"{len(foundation)} Foundation modules inspected; {CLOCK_SEAM} defines the clock seam",
    )


def _pl_4(e: CertificationEvidence) -> ControlResult:
    bad = []
    for p, s in e.code.items():
        if "adapter" in p.name or "provider" in p.name:
            continue
        for vendor in _imported_modules(s) & set(VENDOR_ROOTS):
            bad.append(f"{p.relative_to(REPO_ROOT)} imports {vendor}")
    return _bad("PL-4", bad, len(e.code))


def _st_1(e: CertificationEvidence) -> ControlResult:
    """**Creating** a financial value client-side — a total, an aggregation, a tax, a percentage.

    Deliberately distinct from ST-3. Rendering a figure the server sent is presentation; deriving a
    new one is a second opinion about money the Ledger owns, and only the second is blocking."""
    banned = (".reduce(", "toFixed", "parseFloat")
    bad = []
    pages = e.studio_pages()
    for p, html in pages.items():
        if "<script>" not in html:
            continue
        script = html[html.index("<script>") : html.rindex("</script>")]
        bad.extend(f"{p.name}:{t}" for t in banned if t in script)
    if not pages:
        return ControlResult(
            "ST-1", ControlOutcome.INCONCLUSIVE, "no Studio pages found on disk", (), 0
        )
    return _bad("ST-1", bad, len(pages), f"{len(pages)} Studio pages inspected")


def _st_3(e: CertificationEvidence) -> ControlResult:
    """Client-side currency rescaling with a hardcoded exponent.

    Every currency this platform supports today has exponent 2, so `minor / 100` is correct and the
    finding is **latent**. It becomes a wrong number on screen the day a 0-exponent (JPY) or
    3-exponent (KWD) currency is added — which is why it is recorded with that trigger rather than
    passed over."""
    import re as _re

    bad = []
    pages = e.studio_pages()
    for p, html in pages.items():
        if "<script>" not in html:
            continue
        script = html[html.index("<script>") : html.rindex("</script>")]
        if _re.search(r"(minor|amount)\w*\s*[/%]\s*100\b", script) or _re.search(
            r"[/%]\s*100\b", script
        ):
            bad.append(f"{p.name}: rescales minor units by a hardcoded 100")
    if not pages:
        return ControlResult(
            "ST-3", ControlOutcome.INCONCLUSIVE, "no Studio pages found on disk", (), 0
        )
    return _bad("ST-3", bad, len(pages), "every supported currency has exponent 2 today")


def _st_2(e: CertificationEvidence) -> ControlResult:
    banned = ("localStorage", "sessionStorage", "document.cookie", "indexedDB")
    bad = []
    pages = e.studio_pages()
    for p, html in pages.items():
        if "<script>" not in html:
            continue
        script = html[html.index("<script>") : html.rindex("</script>")]
        bad.extend(f"{p.name}:{t}" for t in banned if t in script)
    if not pages:
        return ControlResult(
            "ST-2", ControlOutcome.INCONCLUSIVE, "no Studio pages found on disk", (), 0
        )
    return _bad("ST-2", bad, len(pages))


def _defined_names(source: str) -> set[str]:
    """Public names a module **defines itself** — classes, functions and module-level constants.

    Deliberately not `dir(module)`, which would also return everything the module *imported* and
    report every consumer of `Money` as a definer of it. Shadowing is about two modules each
    declaring a thing, and this is how that is distinguished from re-use."""
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return out
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                out.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    out.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                out.add(node.target.id)
    return out


def _package_namespace_claims(package: str) -> dict[str, list[tuple[str, object]]]:
    """Every name that reaches a package's namespace, and which submodule put it there.

    Two routes, and both are checked because either alone has a blind spot:

    * what `__init__.py` **imports** — authoritative for `billing.foundation.X`;
    * what a submodule puts in **`__all__`** — authoritative for `from ... import *`, and present
      even in the 110 of 183 Billing modules that declare no `__all__` at all.

    Module-local helpers are deliberately excluded: two engines each defining their own private
    `activity_view` never share a namespace, and flagging them would fire 39 times on a non-hazard
    and teach readers to ignore the control."""
    pkg = importlib.import_module(package)
    claims: dict[str, list[tuple[str, object]]] = {}

    init = Path(pkg.__file__)
    for node in ast.walk(ast.parse(init.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                obj = getattr(importlib.import_module(f"{package}.{node.module}"), name)
                claims.setdefault(name, []).append((node.module, obj))

    # Unguarded for the same reason as `_billing_model_modules`: a module this scan cannot import
    # is a module it cannot certify, and that must surface as INCONCLUSIVE rather than as silent
    # coverage loss. NS-1's first draft was blind to 110 of 183 modules while reporting a
    # reassuring symbol count; this is the same failure wearing a `try` instead of a `getattr`.
    for info in sorted(m.name for m in pkgutil.iter_modules(pkg.__path__)):
        sub = importlib.import_module(f"{package}.{info}")
        for name in getattr(sub, "__all__", ()) or ():
            obj = getattr(sub, name, None)
            if obj is not None:
                claims.setdefault(name, []).append((info, obj))
    return claims


def _ns_1(e: CertificationEvidence) -> ControlResult:
    """**True shadowing**, not duplicate strings.

    A name is a violation only when two Billing modules put *two different objects* under it in one
    package namespace — the condition under which `from billing.foundation import X` silently
    serves whichever module was imported last. Re-exporting one shared object under one name is
    fine and common; two objects under one name is the defect M9.20, M9.22 and M9.23 each shipped.

    Coverage does not depend on `__all__`: an earlier draft used it alone and was blind to
    `commercial_reporting` and 109 other modules, reporting a reassuring 1,405 symbols while never
    inspecting more than half the plane."""
    bad = []
    checked = 0
    for package in ("billing.foundation", "billing.application", "billing.persistence"):
        for name, entries in sorted(_package_namespace_claims(package).items()):
            checked += 1
            distinct = {id(obj): module for module, obj in entries}
            if len(distinct) > 1:
                bad.append(f"{package}.{name}: {sorted(distinct.values())}")
    return _bad("NS-1", bad, checked, f"{checked} package-namespace names inspected")


def _ns_2(e: CertificationEvidence) -> ControlResult:
    """Billing's index names, checked for collision against **every** table in the process.

    The comparison deliberately spans all planes: an index name colliding with another plane's
    would fail `create_all` just as surely as one colliding within Billing.

    `examined` counts **Billing's** indexes only, and that distinction is load-bearing. Counting
    every index in the process made the number — and therefore the certification digest — depend
    on which other planes a given run happened to import: 1,193 in isolation, 1,199 once
    `api.models` registered its five tables. M9.25 found that when it tried to pin the digest as
    freeze evidence. The control's strength is unchanged; only what it reports having examined is
    now deterministic. (M9.25 D-1.)"""
    # Every table in the process that claims each index name. A *set*, not a first-writer-wins
    # map: an earlier draft used `setdefault`, which let Billing's own table register itself as
    # the owner and silently stopped detecting cross-plane collisions entirely.
    owners: dict[str, set[str]] = {}
    for table in e.all_tables.values():
        for index in table.indexes:
            owners.setdefault(index.name, set()).add(table.name)

    bad = []
    count = 0
    for table in e.tables.values():
        for index in table.indexes:
            count += 1
            claimants = owners.get(index.name, {table.name}) | {table.name}
            if len(claimants) > 1:
                bad.append(f"{index.name}: {' vs '.join(sorted(claimants))}")
    return _bad("NS-2", bad, count, f"{count} Billing indexes inspected")


def _ns_3(e: CertificationEvidence) -> ControlResult:
    bad = [n for n in e.tables if not n.startswith("cp_billing_")]
    return _bad("NS-3", bad, len(e.tables))


def _ns_4(e: CertificationEvidence) -> ControlResult:
    import billing.customer.routers as routers

    seen: dict[str, str] = {}
    bad = []
    count = 0
    for name in dir(routers):
        module = getattr(routers, name)
        router = getattr(module, "router", None)
        if router is None:
            continue
        prefix = getattr(router, "prefix", "")
        for route in router.routes:
            for method in sorted(getattr(route, "methods", ()) or ()):
                key = f"{method} {prefix}{route.path}"
                count += 1
                if key in seen and seen[key] != name:
                    bad.append(f"{key}: {seen[key]} vs {name}")
                seen[key] = name
    return _bad("NS-4", bad, count, f"{count} routes inspected")


def _ns_5(e: CertificationEvidence) -> ControlResult:
    """Module-qualified OpenAPI names mean two Pydantic models share a class name.

    **Advisory, deliberately.** FastAPI disambiguates correctly, so the wire contract is unambiguous
    and no client is misled — the cost is a documentation surface that reads badly. The severity is
    recorded in the catalogue and argued in the milestone report rather than decided here."""
    from billing.customer.app import create_billing_app
    from billing.customer.config import BillingApiSettings

    try:
        app = create_billing_app(settings=BillingApiSettings(jwt_secret="c" * 32))
        schemas = app.openapi()["components"]["schemas"]
    except Exception as exc:  # pragma: no cover - surfaced as inconclusive, never as a pass
        return ControlResult(
            "NS-5", ControlOutcome.INCONCLUSIVE, f"the OpenAPI document could not be built: {exc}"
        )
    bad = [k for k in schemas if "__" in k]
    return _bad("NS-5", bad, len(schemas), f"{len(schemas)} schema names inspected")


def _ns_6(e: CertificationEvidence) -> ControlResult:
    import collections

    bad = []
    checked = 0
    for package in ("billing.foundation", "billing.application"):
        exported = importlib.import_module(package).__all__
        checked += len(exported)
        counts = collections.Counter(exported)
        bad.extend(f"{package}.{n} listed {v}x" for n, v in counts.items() if v > 1)
    return _bad("NS-6", bad, checked)


def _dt_1(e: CertificationEvidence) -> ControlResult:
    """Every lifecycle reaches every declared state and has at least one terminal."""
    bad = []
    for name, machine in e.lifecycles.items():
        reachable = {s for (s, _t) in ((s, t) for s in machine.states for t in
            machine.triggers_from(s))}
        targets = {
            machine.get(s, t).target for s in machine.states for t in machine.triggers_from(s)
        }
        sources = {s for s in machine.states if machine.triggers_from(s)}
        orphans = machine.states - targets - sources
        if orphans:
            bad.append(f"{name}: unreachable and inert {sorted(str(o) for o in orphans)}")
        if not machine.terminal_states():
            bad.append(f"{name}: no terminal state")
        del reachable
    return _bad("DT-1", bad, len(e.lifecycles), f"{len(e.lifecycles)} lifecycles inspected")


def _dt_2(e: CertificationEvidence) -> ControlResult:
    """No `derive_*` identity function reads a clock, a UUID or a random source."""
    bad = []
    checked = 0
    for p, s in e.code.items():
        try:
            tree = ast.parse(s)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("derive_"):
                continue
            checked += 1
            body = ast.dump(node)
            for token in ("uuid4", "'random'", "time'", "'now'", "'utcnow'"):
                if token in body:
                    bad.append(f"{p.relative_to(REPO_ROOT)}:{node.name} uses {token}")
    return _bad("DT-2", bad, checked, f"{checked} derive_* identity functions inspected")


def _dt_3(e: CertificationEvidence) -> ControlResult:
    """Exactly one module declares `canonical_form`."""
    owners = []
    for p, s in e.code.items():
        try:
            tree = ast.parse(s)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "canonical_form":
                owners.append(str(p.relative_to(REPO_ROOT)))
    if len(owners) == 1:
        return _ok("DT-3", 1, f"the single canonicalizer lives in {owners[0]}")
    if not owners:
        return ControlResult(
            "DT-3", ControlOutcome.INCONCLUSIVE, "no canonical_form definition was found"
        )
    return _bad("DT-3", owners, len(owners), "more than one canonicalizer exists")


def _dt_4(e: CertificationEvidence) -> ControlResult:
    from billing.persistence import migrations

    versions = [m.VERSION for m in migrations.MIGRATIONS]
    bad = []
    if versions != sorted(versions):
        bad.append(f"chain is not ordered: {versions}")
    if len(set(versions)) != len(versions):
        bad.append("chain contains duplicate versions")
    numbers = [int(v) for v in versions if v.isdigit()]
    if numbers and numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        bad.append(f"chain is not contiguous: {versions}")
    for module in migrations.MIGRATIONS:
        source = _code_only(Path(module.__file__).read_text())
        if "ALTER" in source.upper() or "DROP" in source.upper():
            bad.append(f"{module.VERSION} is not additive")
    return _bad("DT-4", bad, len(versions), f"migrations {versions[0]}..{versions[-1]}")


#: Every control's evaluator, keyed by control id. Asserted complete against the catalogue, so a
#: control cannot be declared and quietly never run.
EVALUATORS = {
    "OWN-1": _own_1, "OWN-2": _own_2, "OWN-3": _own_3, "OWN-4": _own_4,
    "RS-1": _rs_1, "RS-5": _rs_5, "RS-6": _rs_6,
    "PY-5": _py_5, "PY-6": _py_6,
    "TX-1": _tx_1,
    "PL-1": _pl_1, "PL-2": _pl_2, "PL-3": _pl_3, "PL-4": _pl_4,
    "ST-1": _st_1, "ST-2": _st_2, "ST-3": _st_3,
    "NS-1": _ns_1, "NS-2": _ns_2, "NS-3": _ns_3, "NS-4": _ns_4, "NS-5": _ns_5, "NS-6": _ns_6,
    "DT-1": _dt_1, "DT-2": _dt_2, "DT-3": _dt_3, "DT-4": _dt_4,
}


def certify() -> CertificationReport:
    """Run every declared control and return the verdict.

    An evaluator that raises becomes `INCONCLUSIVE` with the exception recorded — **never a pass**.
    That is the single most important line in this module: a certification suite that swallowed its
    own failures as successes would be worse than no certification, because it would be believed."""
    evidence = CertificationEvidence()
    results = []
    for control in CERTIFICATION_CONTROLS:
        evaluator = EVALUATORS.get(control.id)
        if evaluator is None:
            results.append(
                ControlResult(
                    control.id,
                    ControlOutcome.INCONCLUSIVE,
                    "the control is declared but has no evaluator",
                )
            )
            continue
        try:
            results.append(evaluator(evidence))
        # Deliberately broad: any evaluator failure becomes INCONCLUSIVE, never a pass.
        except Exception as exc:  # noqa: BLE001
            results.append(
                ControlResult(
                    control.id,
                    ControlOutcome.INCONCLUSIVE,
                    f"the evaluator raised {type(exc).__name__}: {exc}",
                )
            )
    return CertificationService.report(results)


def render(report: CertificationReport) -> str:
    """The certification report as stable text. Deterministic: no clock, no host, no run order."""
    lines = [
        f"certification_version={report.certification_version}",
        f"verdict={report.verdict.value}",
        f"digest={report.report_digest}",
        f"controls={len(report.results)}",
        "",
    ]
    for result in report.results:
        from tests.certification.catalogue import CONTROLS_BY_ID

        control = CONTROLS_BY_ID[result.control_id]
        lines.append(
            f"{result.control_id:6} {result.outcome.value:14} "
            f"{control.severity.value:8} examined={result.examined:<5} {control.statement}"
        )
        for violation in result.violations:
            lines.append(f"       ! {violation}")
        if result.detail and result.outcome is not ControlOutcome.SATISFIED:
            lines.append(f"       - {result.detail}")
    return "\n".join(lines)


__all__ = [
    "BILLING_ROOT",
    "EVALUATORS",
    "FORBIDDEN_PLANES",
    "REPO_ROOT",
    "VENDOR_ROOTS",
    "CertificationEvidence",
    "certify",
    "render",
]
