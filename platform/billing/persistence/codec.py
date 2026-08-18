"""Aggregate codec (M9.4) — the one place a Foundation aggregate becomes JSON and back.

Every Billing aggregate is a frozen, slotted dataclass built from a closed set of value
kinds: primitives, `StrEnum`s, `Money`, nested frozen dataclasses, homogeneous tuples, and
one plain `dict` (event data). That regularity is what makes a *single* type-driven codec
correct for all thirty aggregates, instead of thirty hand-written field lists that drift
the moment the domain gains a field.

**Round-trip fidelity is the contract**: `decode(type(a), encode(a)) == a` for every
aggregate. The persistence tests assert exactly that, aggregate by aggregate, because a
mapping layer that is merely *plausible* is how revenue records quietly lose a field.

Decoding is **tolerant forward, strict backward**: unknown keys in a stored payload are
ignored (a field removed by a later Foundation version does not break old rows) and absent
keys fall back to the dataclass default (a field added by a later version reads correctly
from rows written before it existed). That is what lets the payload column absorb additive
Foundation change without a migration.

This module knows nothing about SQL, sessions, or tables — it is pure translation.
"""

from __future__ import annotations

import types
import typing
from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from typing import Any, get_args, get_origin

_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    """Resolved type hints for a dataclass, cached. Aggregates use
    `from __future__ import annotations`, so raw `__annotations__` are strings."""
    cached = _HINTS.get(cls)
    if cached is None:
        cached = typing.get_type_hints(cls)
        _HINTS[cls] = cached
    return cached


def _unwrap_optional(tp: Any) -> Any:
    """`X | None` → `X`. Aggregates use optionals but never multi-arm unions."""
    if get_origin(tp) in (types.UnionType, typing.Union):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def encode(value: Any) -> Any:
    """Aggregate → JSON-portable structure. Tuples become lists (JSON has no tuple);
    `decode` restores them from the declared field type."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    return value  # str | int | float | bool | None


def decode(tp: Any, raw: Any) -> Any:
    """JSON structure → aggregate of declared type `tp`."""
    if raw is None:
        return None
    tp = _unwrap_optional(tp)
    origin = get_origin(tp)

    if origin is tuple:
        args = get_args(tp)
        item_t = args[0] if args else Any
        return tuple(decode(item_t, v) for v in raw)
    if origin in (list, set, frozenset):
        args = get_args(tp)
        item_t = args[0] if args else Any
        built = [decode(item_t, v) for v in raw]
        return origin(built)
    if origin is dict:
        args = get_args(tp)
        val_t = args[1] if len(args) == 2 else Any
        return {k: decode(val_t, v) for k, v in raw.items()}

    if isinstance(tp, type):
        if issubclass(tp, Enum):
            return tp(raw)
        if is_dataclass(tp):
            hints = _hints(tp)
            kwargs: dict[str, Any] = {}
            for f in fields(tp):
                if f.name not in raw:
                    # Absent: let the dataclass default apply. A field with no default is
                    # required — surfacing the TypeError beats silently inventing a value.
                    if f.default is MISSING and f.default_factory is MISSING:  # type: ignore[misc]
                        raise KeyError(
                            f"{tp.__name__}.{f.name} missing from stored payload and has no default"
                        )
                    continue
                kwargs[f.name] = decode(hints[f.name], raw[f.name])
            return tp(**kwargs)
        if tp is float and isinstance(raw, int):
            return float(raw)  # JSON/SQLite may narrow 1.0 to 1

    return raw
