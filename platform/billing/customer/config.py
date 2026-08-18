"""Billing Control Plane API configuration (M9.5).

Environment-driven settings for the Billing HTTP transport. Frozen value object, loaded from
`VOXVOICE_BILLING_*` variables with safe development defaults and validated at startup — a
production environment may not run on the default JWT secret (fail fast, §A2.6).

The JWT settings deliberately mirror the Epic 8 Control Plane's: **the same identity tokens
verify here**, because Billing consumes the platform's identity and issues none of its own
(ADR-0023 §5.3). `VOXVOICE_BILLING_JWT_SECRET` falls back to `VOXVOICE_API_JWT_SECRET` so a
deployment configures one identity secret, not two that can silently diverge.

Transport/config only — no business logic, no pricing, no policy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from core.errors import ConfigError

_DEFAULT_SECRET = "dev-secret-change-me"


@dataclass(frozen=True, slots=True)
class BillingApiSettings:
    environment: str = "development"  # development | testing | production
    database_url: str = "sqlite://"
    jwt_secret: str = _DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_secondary_secrets: tuple[str, ...] = ()  # key-rotation seam (old keys still verify)
    cors_origins: tuple[str, ...] = ("*",)
    api_title: str = "VoxVoice Billing Control Plane API"
    api_version: str = "1.0.0"
    api_prefix: str = "/v1/billing"
    max_request_bytes: int = 4_000_000

    @classmethod
    def load(cls, **overrides: object) -> BillingApiSettings:
        env = os.environ
        origins = env.get("VOXVOICE_BILLING_CORS_ORIGINS")
        secondary = env.get("VOXVOICE_BILLING_JWT_SECONDARY_SECRETS")
        base = cls(
            environment=env.get("VOXVOICE_BILLING_ENV", "development"),
            database_url=env.get("VOXVOICE_BILLING_DATABASE_URL", "sqlite://"),
            # One identity secret for the platform — see module docstring.
            jwt_secret=env.get(
                "VOXVOICE_BILLING_JWT_SECRET",
                env.get("VOXVOICE_API_JWT_SECRET", _DEFAULT_SECRET),
            ),
            jwt_secondary_secrets=(
                tuple(s.strip() for s in secondary.split(",") if s.strip()) if secondary else ()
            ),
            cors_origins=tuple(o.strip() for o in origins.split(",")) if origins else ("*",),
            max_request_bytes=int(env.get("VOXVOICE_BILLING_MAX_REQUEST_BYTES", "4000000")),
        )
        settings = replace(base, **overrides) if overrides else base
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in ("development", "testing", "production"):
            raise ConfigError(f"invalid environment {self.environment!r}")
        if self.max_request_bytes <= 0:
            raise ConfigError("max_request_bytes must be positive")
        if not self.api_prefix.startswith("/"):
            raise ConfigError("api_prefix must start with '/'")
        if self.is_production:
            # Production fails fast on insecure defaults (§A2.6 — declared, never silent).
            if self.jwt_secret == _DEFAULT_SECRET:
                raise ConfigError(
                    "production Billing API must set VOXVOICE_BILLING_JWT_SECRET "
                    "(or VOXVOICE_API_JWT_SECRET), not the default"
                )
            if "*" in self.cors_origins:
                raise ConfigError("production Billing API must not allow wildcard CORS origins")
            if self.database_url in ("sqlite://", "sqlite:///:memory:"):
                raise ConfigError(
                    "production Billing API must use a durable database, not in-memory sqlite"
                )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def verification_secrets(self) -> tuple[str, ...]:
        """Secrets a token may verify against (primary + rotated) — the rotation seam."""
        return (self.jwt_secret, *self.jwt_secondary_secrets)


# OpenAPI tag grouping — one tag per Billing bounded context (transport concern only).
OPENAPI_TAGS: list[dict] = [
    {
        "name": "accounts",
        "description": "Billing accounts — commercial identity, hierarchy, payers, contacts, "
        "addresses, preferences, credit profile, and commercial status. Who we bill; never "
        "what they bought or what they owe.",
    },
    {"name": "catalog", "description": "Plans, features, and meter definitions."},
    {
        "name": "pricing",
        "description": "Price books, effective-dated prices, discounts, promotions, "
        "coupons, and deterministic price resolution. Prices only — a charge is rating.",
    },
    {
        "name": "subscriptions",
        "description": "The commercial agreement: lifecycle, items, billing cycle, trials, "
        "renewal, pause, cancellation, scheduled changes, and immutable versions. What was "
        "agreed; never what it costs.",
    },
    {
        "name": "entitlements",
        "description": "Commercial capability: declared gates with fail-open / fail-closed "
        "classifications, bundles, precedence-resolved grants, effective dating, immutable "
        "versioned snapshots, staleness directives, inspection, and quota verdicts. Billing "
        "decides and publishes; the owning platform enforces locally. Never authorization.",
    },
    {
        "name": "metering",
        "description": "Commercial usage: meter definitions with one authoritative source, "
        "idempotent ingestion keyed by source-event identity, normalization, attribution, "
        "windowed aggregation, sealed periods, late-arrival adjustments, corrections, "
        "quarantine, deterministic replay, reconciliation, provenance, and exports. "
        "**Billing never measures** — it consumes the authoritative metric.* stream.",
    },
    {
        "name": "rating",
        "description": "Commercial charge calculation: flat, seat, usage, tiered, volume, "
        "package and hybrid charges with full breakdown and provenance, included quantities, "
        "overage, minimum commitments, discount application, deterministic rating runs keyed by "
        "an input digest, idempotent rerating, adjustment against finalized runs, comparison, "
        "explanation, snapshots, and append-only history. **Billing never prices** — it rates "
        "immutable inputs.",
    },
    {
        "name": "ledger",
        "description": "Append-only revenue ledger and balances: immutable journal entries with "
        "exactly-once posting keyed by what caused them, balances **derived** on every read and "
        "never stored, accounting periods with a one-way close and a sealed content digest that a "
        "replay proves against, corrections by reversal and adjustment rather than mutation, "
        "credits whose remaining value is derived from their applications, a trial-balance control "
        "report, reconciliation against rated charges, and append-only history. The Ledger "
        "**records** what other domains decided — it prices nothing, rates nothing, invoices "
        "nothing, and moves no money.",
    },
    {"name": "credits", "description": "Credit grants and credit balance."},
    {
        "name": "invoices",
        "description": "Customer documents: invoices, credit notes and debit notes with gapless, "
        "jurisdiction-aware numbering allocated inside the issuing transaction, immutable "
        "content-addressed snapshots that reproduce byte-identically forever, append-only revision "
        "history, corrections by credit and debit note rather than by editing, document "
        "relationships, deterministic HTML and PDF rendering that computes nothing, delivery and "
        "attachment metadata, export, and reconciliation against the ledger. The Invoice "
        "**presents** what other domains decided — it prices nothing, rates nothing, posts no "
        "ledger entry, moves no money, and determines no tax.",
    },
    {
        "name": "payments",
        "description": "Payment execution: collection objectives, append-only attempts, the "
        "durable provider-operation claim that makes one collection exactly one external money "
        "movement, verified provider callbacks, refunds as new financial facts, and "
        "reconciliation of anything Billing and a provider disagree about. **The provider moves "
        "money; Billing owns the fact that it moved.** No provider is named in this codebase — "
        "routing is operator-declared — and a payment explains itself from stored evidence "
        "without calling a provider again. It prices nothing, rates nothing, taxes nothing, "
        "computes no invoice total, and keeps no balance: a capture crosses into the Ledger "
        "through the Ledger's own command.",
    },
    {
        "name": "collections",
        "description": "Dunning & collections: versioned recovery strategies, one case per "
        "obligation, durable actions decided before anything leaves the building, append-only "
        "execution evidence, and a decision trail that replays after the strategy is rewritten. "
        "**Collections decides whether to ask; Payments moves the money.** It reaches no "
        "provider, posts no ledger entry, recalculates no invoice total, and holds no balance — "
        "there is no amount anywhere in this surface. An *ambiguous* payment outcome is never a "
        "retry candidate: only reconciliation resolves one, and until it does the recovery waits. "
        "Billing runs no scheduler — `/recovery/cases/due` is a read, and whoever polls it lives "
        "outside this plane.",
    },
    {
        "name": "revenue",
        "description": "Revenue recognition & deferred revenue: immutable versioned recognition "
        "policies, schedules derived from **issued invoice snapshots**, dated period entries, "
        "corrections as new facts, and a decision trail that replays after the policy is "
        "rewritten. **Invoice states what was billed; Recognition decides when it is earned; "
        "Ledger records the fact.** Deferred revenue is never a stored balance — it is total "
        "minus recognized minus adjusted, derived on every request (ADR-0023 RS6: recognition is "
        "a projection over the ledger, not a second set of books). **Paid is not earned**: no "
        "payment or collection state is read anywhere, so a paid invoice may sit fully deferred "
        "and an unpaid one may be fully recognized. Every split goes through the same "
        "largest-remainder allocator, so no minor unit is created or lost. There is no DELETE on "
        "this surface, and closed accounting periods are M9.13's rule, reused rather than "
        "restated.",
    },
    {
        "name": "integrity",
        "description": "Reconciliation & financial integrity: six declared relationships between "
        "the domains that own financial facts, compared against each other and recorded as durable "
        "runs, findings and evidence-backed resolutions. **A reconciliation result is an "
        "observation, not permission to mutate another domain.** It posts no ledger entry, moves "
        "no money, issues no document, determines no tax, calculates no recognition and holds no "
        "balance — there is no monetary total anywhere on this surface, only counts. A divergence "
        "is *never* auto-resolved (ADR-0023 PY5): the lifecycle has no edge out of resolved or "
        "closed, and `resolve` re-reads both sides and refuses unless they now agree. Repairs go "
        "to the **owning domain's own command** through its own Application boundary; where no "
        "automatic repair is declared, the owner is named and that is all. Billing runs no "
        "scheduler — `/integrity/findings` is a read, and whoever polls it lives outside this "
        "plane.",
    },
    {
        "name": "investigations",
        "description": "Financial investigation & audit evidence: the operational investigation "
        "of an integrity incident M9.20 already declared, recorded so an auditor can reconstruct "
        "it **without relying on application logs**. It reconciles nothing (M9.19), correlates "
        "nothing (M9.20), calculates no price, rate, tax, total, balance or recognized amount, "
        "and holds no monetary figure — every integer here is a count. It executes no repair and "
        "resolves no incident: there is no outward command on this surface at all. A "
        "`LIVE_REFERENCE` and an `EVIDENCE_SNAPSHOT` are structurally distinct and neither can "
        "become the other; evidence is content-addressed and append-only, so a source becoming "
        "unavailable, redacted or superseded appends a **new link in a chain of custody** rather "
        "than overwriting what was true before. **\"Could not retrieve\" never becomes "
        "\"verified\"** — an unavailable source stays visible as a named gap and makes an audit "
        "package `INCOMPLETE`. A hypothesis cannot become an observed fact, a `CONFIRMED` "
        "conclusion requires an available snapshot, and `INCONCLUSIVE` requires none — so nobody "
        "is ever forced to name a root cause to close a case. Closure is reachable **only** "
        "through a recorded conclusion, and a closed investigation is never reopened. Audit "
        "packages are immutable and content-addressed: exporting unchanged state twice returns "
        "the same package, and any change produces a new one beside it.",
    },
    {
        "name": "reports",
        "description": "Compliance & regulatory reporting: what must be reported, for which "
        "period, under which versioned definition, from which authoritative sources, and what "
        "evidence supports it. **A report is derived evidence, not financial truth** — it creates "
        "no ledger entry, no invoice, no tax determination, no payment and no recognition entry, "
        "and no repository that could is reachable. Every field names its authoritative source and "
        "a transformation drawn from a closed set of seven, exactly one of which touches money; "
        "there is no rate, no FX and **no currency conversion anywhere** — sources spanning "
        "currencies come back unsupported rather than converted. Definitions are **versioned and "
        "immutable**: a rule that changes gets a new version and never reinterprets a filing "
        "somebody already made. Inputs are **pinned**, and the report's identity derives from the "
        "manifest digest — so the same inputs file once, and a moved source produces a new report "
        "*beside* the old one rather than rewriting it. **Missing evidence never becomes "
        "zero**: an unreadable source is a named gap that blocks completeness and finalization, "
        "because a "
        "regulator cannot tell a zero that means nothing happened from one that means we could not "
        "look. Finalization is reachable only from a validation that passed, a finalized report is "
        "never edited, and a correction opens a new filing that both parties can diff. Submission "
        "packages are prepared but **never sent** — there is no network client, no regulator "
        "endpoint and no credential in this milestone.",
    },
    {
        "name": "consolidations",
        "description": "Period scope & consolidation: which accounts belong to a reporting scope, "
        "for which period, at which source revisions — and what could not be included, and why. "
        "**M9.23 owns scope and owns no financial fact** — it creates no ledger entry, no invoice, "
        "no tax determination, no payment and no recognition entry, and it never changes an "
        "account's parent or payer: M9.8's hierarchy is read through a contract with no write "
        "method. Scope rules are **versioned and immutable**, and a resolution is an **immutable "
        "snapshot**: an account that moves parent in March never changes what January's "
        "consolidation covered. Attribution is **never guessed** — a fact that cannot be "
        "attributed to exactly one member is unattributable, not assigned to the payer, the "
        "parent, the newest account or the largest amount. **Missing accounts and unreadable "
        "sources never become zero**: an unavailable source is pinned with no figure at all and a "
        "named exception, and completeness distinguishes complete, partial, blocked and "
        "unavailable. Currencies are **never combined implicitly and never converted** — there is "
        "no FX rate at any altitude. Finalization is reachable only from a validation that passed, "
        "an acknowledged blocking exception still blocks, a finalized consolidation is never "
        "edited, and a correction opens a new one beside it.",
    },
    {"name": "refunds", "description": "Refund requests and approvals."},
    {
        "name": "taxes",
        "description": "Tax determination: jurisdictions, classifications, supplier nexus and "
        "registrations, customer exemptions, effective-dated **rate evidence** with the authority "
        "that set it, deterministic place-of-supply resolution, declared treatment precedence, "
        "integer-only calculation with a recorded rounding policy and scope, attributed overrides, "
        "immutable append-only determinations that supersede rather than edit, an explanation that "
        "answers *why was this tax applied?* **without calling the provider again**, "
        "reconciliation, diagnostics, and an append-only audit trail. Billing ships **no rate "
        "table**: determination is a rented edge behind an adapter port, and what is owned is the "
        "evidence (ADR-0023 TX1). Tax determines the treatment of amounts other domains decided — "
        "it prices nothing, rates nothing, posts no ledger entry, and issues no invoice.",
    },
    {"name": "contracts", "description": "Enterprise contracts and commitments."},
    {"name": "licenses", "description": "Issued licenses and revocation."},
    {"name": "system", "description": "Health, readiness, and version."},
]
