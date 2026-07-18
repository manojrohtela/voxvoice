"""Structured logging (M5.0) — key/value log records with replay correlation.

Wraps the stdlib logger to emit structured fields (session_id, correlation_id,
module, …) so logs correlate with recorded events + traces. No transport/exporter —
formatting/shipping is an extension point.
"""

from __future__ import annotations

import logging
from typing import Any


class StructuredLogger:
    """A thin structured-logging façade over a stdlib logger."""

    def __init__(self, name: str, *, base_fields: dict[str, Any] | None = None) -> None:
        self._log = logging.getLogger(name)
        self._base = dict(base_fields or {})

    def bind(self, **fields: Any) -> StructuredLogger:
        """Return a child logger with additional persistent fields (e.g. session_id)."""
        child = StructuredLogger(self._log.name, base_fields={**self._base, **fields})
        return child

    def _emit(self, level: int, msg: str, fields: dict[str, Any]) -> None:
        merged = {**self._base, **fields}
        self._log.log(level, msg, extra={"structured": merged})

    def info(self, msg: str, **fields: Any) -> None:
        self._emit(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, fields)

    def debug(self, msg: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, msg, fields)
