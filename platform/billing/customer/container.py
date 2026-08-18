"""Dependency-injection container + FastAPI providers (M9.5).

The composition seam for the Billing API. `BillingContainer` holds the wired
`PersistentBillingApplication` (from the M9.4 persistence composition root) and the settings;
the providers pull them off `request.app.state.container`. Every dependency is replaceable —
a test builds a container over an in-memory database and hands it to
`create_billing_app(container=...)`, overriding nothing else.

The API **never** constructs a repository, a Unit of Work, or the Foundation. It asks
`build_persistent_billing_application(...)` to wire them and holds the result.

**The authorizer is an injected dependency with no default.** Billing owns no RBAC, so this
layer cannot supply one; passing `None` leaves the Application's `DenyAllAuthorizer` in place
and every command is refused with 403. That is the intended posture for a deployment that has
not yet wired the Control Plane's §A11.2 evaluator (M9.18): **inert, not open.**
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from billing.application import BillingApplication, BillingAuthorizer
from billing.foundation import Clock, DefaultIdGenerator, IdGenerator, SystemClock
from billing.persistence import (
    BillingDatabase,
    PersistentBillingApplication,
    build_persistent_billing_application,
)

from .config import BillingApiSettings


@dataclass(frozen=True, slots=True)
class BillingContainer:
    settings: BillingApiSettings
    persistent: PersistentBillingApplication
    ids: IdGenerator
    clock: Clock

    @property
    def application(self) -> BillingApplication:
        return self.persistent.application

    @property
    def database(self) -> BillingDatabase:
        return self.persistent.database

    @classmethod
    def build(
        cls,
        settings: BillingApiSettings,
        *,
        authorizer: BillingAuthorizer | None = None,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        payment_providers: object | None = None,
        collection_notifiers: object | None = None,
    ) -> BillingContainer:
        # The same two seams the Application and Foundation were wired with, held here so a
        # controller that must name an aggregate (see `routers/taxes.py`) uses the platform's
        # id and time sources rather than inventing its own. Deterministic in tests by
        # injection, exactly as every layer below.
        ids = ids or DefaultIdGenerator()
        clock = clock or SystemClock()
        persistent = build_persistent_billing_application(
            url=settings.database_url,
            ids=ids,
            clock=clock,
            authorizer=authorizer,
            # M9.16: the payment provider registry. `None` ⇒ an empty registry, which is the
            # correct production default for a plane that ships with no provider: nothing is
            # routed and nothing can be collected until an operator declares both.
            payment_providers=payment_providers,
            # M9.17: the collection notifier registry. `None` ⇒ an empty registry, which
            # sends nothing — the only safe default for outbound customer messaging.
            collection_notifiers=collection_notifiers,
        )
        return cls(settings=settings, persistent=persistent, ids=ids, clock=clock)

    def check_database(self) -> bool:
        """Readiness probe support: is the database actually reachable?

        Lives here rather than in the system router so a controller never touches storage —
        and it opens a connection through the session the persistence layer owns rather than
        issuing SQL, so the transport layer has no SQL of its own to drift."""
        try:
            self.database.session.connection()
        except Exception:  # noqa: BLE001 — a probe reports, it never raises
            return False
        return True


# ── FastAPI providers (all replaceable via dependency_overrides / app.state) ─────


def get_container(request: Request) -> BillingContainer:
    return request.app.state.container


def get_settings(container: BillingContainer = Depends(get_container)) -> BillingApiSettings:
    return container.settings


def get_application(container: BillingContainer = Depends(get_container)) -> BillingApplication:
    return container.application


def get_ids(container: BillingContainer = Depends(get_container)) -> IdGenerator:
    return container.ids


def get_clock(container: BillingContainer = Depends(get_container)) -> Clock:
    return container.clock
