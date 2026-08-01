"""Repository query helpers (M8.4) — reusable, org-scoped SELECT builders.

Pure data-access helpers, **no business logic**: they only construct/execute scoped
queries so the repositories stay terse and every read is org-filtered by construction
(Constitution §A11.2 — isolation is not optional). Nothing here interprets a result,
authorizes, validates, or mutates.
"""

from __future__ import annotations

from typing import TypeVar

from sqlmodel import Session, select

T = TypeVar("T")


def get_by_pk(session: Session, model: type[T], pk: object) -> T | None:
    """Primary-key lookup (identity-map fast path)."""
    return session.get(model, pk)


def first_where(session: Session, model: type[T], **equals: object) -> T | None:
    """First row matching all equality filters (deterministic: first by table order)."""
    statement = select(model)
    for field, value in equals.items():
        statement = statement.where(getattr(model, field) == value)
    return session.exec(statement).first()


def all_where(session: Session, model: type[T], **equals: object) -> list[T]:
    """All rows matching all equality filters — always pass `org_id` for isolation."""
    statement = select(model)
    for field, value in equals.items():
        statement = statement.where(getattr(model, field) == value)
    return list(session.exec(statement).all())
