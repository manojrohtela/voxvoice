"""Authorization orchestration (M9.3).

ADR-0023 §7/§11.3, restated at the orchestration altitude: **Billing never owns RBAC,
never owns authentication, and never evaluates a permission.** It *coordinates* an
authorization seam supplied from outside.

The seam is an ABC declared here rather than an import of the Epic 8 RBAC evaluator,
because ADR-0031 IR1/IR3 confine this plane to `core` + `billing` + stdlib. The Control
Plane implements `BillingAuthorizer` by delegating to the existing §A11.2 RBAC evaluator
at the composition root (M9.4/M9.18). Billing therefore names *what it requires* and never
decides *who may*.

`BillingPermission` is a vocabulary of required capabilities, not a permission model: the
values map onto existing Control-Plane scopes at wiring time. No role, no grant, and no
evaluation rule exists in this plane.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from .errors import BillingAuthorizationError


class BillingPermission(StrEnum):
    """What a billing use case *requires*. Mapped to Control-Plane RBAC scopes at wiring
    time (the Billing Admin / Org Owner roles Epic 8 already defines)."""

    BILLING_READ = "billing.read"
    BILLING_MANAGE = "billing.manage"  # plan/subscription/payment-method changes
    BILLING_CATALOG_MANAGE = "billing.catalog.manage"  # platform catalog + price books
    BILLING_INVOICE_ISSUE = "billing.invoice.issue"
    BILLING_REFUND_REQUEST = "billing.refund.request"
    BILLING_REFUND_APPROVE = "billing.refund.approve"  # separation of duties
    BILLING_CONTRACT_MANAGE = "billing.contract.manage"
    BILLING_USAGE_INGEST = "billing.usage.ingest"  # system actor (metering pipeline)

    # ── M9.21 · financial investigation & audit evidence ────────────────────────
    #
    # Five, rather than folding investigation onto `BILLING_MANAGE`, because these capabilities are
    # genuinely separate from the ones above and from each other. Reading a case is not conducting
    # one; capturing evidence is not concluding; and **exporting an audit package is the one action
    # here that leaves the building** — it produces the artifact an external examiner reads, so it
    # gets a scope somebody can grant to an auditor and to nobody else.
    #
    # Still a vocabulary of *required capabilities*, not a permission model: no role, no grant and
    # no evaluation rule exists in this plane, and the values map onto Control-Plane scopes at
    # wiring time exactly as the eight above do.
    BILLING_INVESTIGATION_READ = "billing.investigation.read"
    BILLING_INVESTIGATION_OPEN = "billing.investigation.open"
    BILLING_INVESTIGATION_CONDUCT = "billing.investigation.conduct"
    BILLING_INVESTIGATION_EVIDENCE = "billing.investigation.evidence"
    BILLING_INVESTIGATION_CONCLUDE = "billing.investigation.conclude"  # elevated
    BILLING_INVESTIGATION_CLOSE = "billing.investigation.close"  # elevated
    BILLING_AUDIT_EXPORT = "billing.audit.export"

    # ── M9.22 · compliance & regulatory reporting ───────────────────────────────
    #
    # Seven, because filing to a regulator is not one capability. Generating a draft is routine;
    # **finalizing** produces an artifact somebody outside the company will read, and correcting a
    # filed report says the previous one was wrong — both deserve scopes an organization can grant
    # separately from day-to-day reporting work.
    BILLING_REPORT_READ = "billing.report.read"
    BILLING_REPORT_GENERATE = "billing.report.generate"
    BILLING_REPORT_VALIDATE = "billing.report.validate"
    BILLING_REPORT_FINALIZE = "billing.report.finalize"  # elevated
    BILLING_REPORT_CORRECT = "billing.report.correct"  # elevated
    BILLING_REPORT_EXPORT = "billing.report.export"

    # ── M9.23 · period scope & consolidation ────────────────────────────────────
    #
    # Seven, mirroring M9.22's shape for the same reason: consolidating a group of accounts is not
    # one capability. **Managing a scope decides which accounts a regulator will be told about** —
    # a strictly larger power than generating a run over a scope somebody else defined — and
    # finalizing produces the artifact a filing consumes. Both deserve scopes an organization can
    # grant separately from day-to-day consolidation work.
    #
    # Still a vocabulary of *required capabilities*, not a permission model: no role, no grant and
    # no evaluation rule exists in this plane.
    BILLING_CONSOLIDATION_READ = "billing.consolidation.read"
    BILLING_CONSOLIDATION_SCOPE_MANAGE = "billing.consolidation.scope.manage"  # elevated
    BILLING_CONSOLIDATION_GENERATE = "billing.consolidation.generate"
    BILLING_CONSOLIDATION_VALIDATE = "billing.consolidation.validate"
    BILLING_CONSOLIDATION_FINALIZE = "billing.consolidation.finalize"  # elevated
    BILLING_CONSOLIDATION_CORRECT = "billing.consolidation.correct"  # elevated
    BILLING_CONSOLIDATION_EXPORT = "billing.consolidation.export"
    BILLING_SUBMISSION_PACKAGE_READ = "billing.report.submission_package.read"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """The immutable question put to the seam. Carries no answer and no policy."""

    actor_id: str
    org_id: str
    permission: BillingPermission
    resource_id: str = ""


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """The seam's answer. Billing records it; Billing never computes it."""

    allowed: bool
    reason: str = ""


class BillingAuthorizer(ABC):
    """The authorization seam. Implemented **outside** this plane."""

    @abstractmethod
    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


class DenyAllAuthorizer(BillingAuthorizer):
    """The safe default when no seam is wired: deny, loudly. A billing plane that
    silently allowed everything because wiring was forgotten is the worse failure."""

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision(False, "no authorization seam is wired")


class AllowAllAuthorizer(BillingAuthorizer):
    """For tests and pure-domain exercises only — never a production wiring."""

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision(True, "test authorizer")


class AuthorizationOrchestrator:
    """Thin coordination over the seam: ask, and raise a typed error on deny.

    Contains no permission logic — the whole class is a call, a check, and an error. That
    is the point: it is auditable at a glance that Billing decides nothing here."""

    def __init__(self, authorizer: BillingAuthorizer) -> None:
        self._authorizer = authorizer

    def require(
        self,
        actor_id: str,
        org_id: str,
        permission: BillingPermission,
        *,
        resource_id: str = "",
    ) -> AuthorizationDecision:
        decision = self._authorizer.authorize(
            AuthorizationRequest(actor_id, org_id, permission, resource_id)
        )
        if not decision.allowed:
            raise BillingAuthorizationError(
                f"{actor_id or 'anonymous'!r} denied {permission.value} in org {org_id!r}"
                f"{': ' + decision.reason if decision.reason else ''}"
            )
        return decision
