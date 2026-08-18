"""Billing database engine + session lifetime (M9.4).

Owns the SQLAlchemy engine and the one `Session` that the Billing repositories read and
write and that `SqlBillingUnitOfWork` commits or rolls back. Repositories never touch the
engine and never end a transaction — they hold this object and flush; the Unit of Work owns
the boundary (commit-as-you-go, the M8.4 shape).

`create_all()` creates **only** `cp_billing_*` tables. Billing brings its own `Database`
rather than importing the Epic 8 one because ADR-0031 IR3 forbids `billing` importing
`api` — and because it is what keeps the plane extractable into its own service and its own
database later as a deployment decision rather than a re-architecture (ADR-0031 §17).
Pointing it at the same URL as the Control Plane is a *deployment* choice; nothing here
assumes it.

For in-memory SQLite (tests) a `StaticPool` pins one connection so schema and data survive
for the object's lifetime. Any SQLAlchemy URL (Postgres/Supabase) works unchanged.
"""

from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from .account_models import ACCOUNT_TABLES
from .catalog_models import CATALOG_TABLES
from .collection_models import COLLECTION_TABLES
from .consolidation_models import CONSOLIDATION_TABLES
from .entitlement_models import ENTITLEMENT_TABLES
from .incident_models import INCIDENT_TABLES
from .investigation_models import INVESTIGATION_TABLES
from .invoice_models import INVOICE_TABLES
from .ledger_models import LEDGER_TABLES
from .metering_models import METERING_TABLES
from .models import BILLING_TABLES
from .payment_models import PAYMENT_TABLES
from .pricing_models import PRICING_TABLES
from .rating_models import RATING_TABLES
from .reconciliation_models import RECONCILIATION_TABLES
from .reporting_models import REPORTING_TABLES
from .revenue_models import REVENUE_TABLES
from .subscription_models import SUBSCRIPTION_TABLES
from .tax_models import TAX_TABLES

# Additive, one milestone at a time: M9.4 core, M9.6 catalog, M9.7 pricing, M9.8 accounts,
# M9.9 subscriptions, M9.10 entitlements, M9.11 metering, M9.12 rating, M9.13 ledger,
# M9.14 invoicing, M9.15 tax, M9.16 payments, M9.17 collections, M9.18 revenue recognition.
ALL_BILLING_TABLES = (
    *BILLING_TABLES,
    *CATALOG_TABLES,
    *PRICING_TABLES,
    *ACCOUNT_TABLES,
    *SUBSCRIPTION_TABLES,
    *ENTITLEMENT_TABLES,
    *METERING_TABLES,
    *RATING_TABLES,
    *LEDGER_TABLES,
    *INVOICE_TABLES,
    *TAX_TABLES,
    *PAYMENT_TABLES,
    *COLLECTION_TABLES,
    *REVENUE_TABLES,
    *RECONCILIATION_TABLES,
    *INCIDENT_TABLES,
    *INVESTIGATION_TABLES,
    *REPORTING_TABLES,
    *CONSOLIDATION_TABLES,
)


class BillingDatabase:
    def __init__(self, url: str = "sqlite://", *, echo: bool = False) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_kwargs: dict = {"echo": echo, "connect_args": connect_args}
        if url in ("sqlite://", "sqlite:///:memory:"):
            engine_kwargs["poolclass"] = StaticPool
        self.url = url
        self.engine = create_engine(url, **engine_kwargs)
        self._session: Session | None = None

    def create_all(self) -> None:
        """Create the `cp_billing_*` tables (idempotent). Touches no other table."""
        SQLModel.metadata.create_all(self.engine, tables=[t.__table__ for t in ALL_BILLING_TABLES])

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = Session(self.engine)
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
