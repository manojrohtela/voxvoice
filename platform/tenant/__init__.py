"""tenant — the multi-tenancy foundation (M5.0).

Tenant/organization context value objects + RBAC / quota / audit / policy
**interfaces** (no implementation, no authentication — non-goals at M5.0). A floor
package (stdlib only); AI runtime modules accept a `TenantContext` + these ports to
enforce isolation uniformly. Tenant isolation is naturally expressible via the
Context Runtime as the single enforcement choke point (per the summit).
"""

from .access import (
    AccessDecision,
    AuditRecord,
    AuditSink,
    Authorizer,
    Effect,
    QuotaCheck,
    QuotaEnforcer,
    TenantPolicy,
)
from .context import OrganizationContext, TenantContext

__all__ = [
    "TenantContext",
    "OrganizationContext",
    "Authorizer",
    "AccessDecision",
    "Effect",
    "QuotaEnforcer",
    "QuotaCheck",
    "AuditSink",
    "AuditRecord",
    "TenantPolicy",
]
