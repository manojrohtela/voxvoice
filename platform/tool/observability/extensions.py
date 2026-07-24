"""Enterprise telemetry extension points (M6.1B-3) — interfaces only. No
implementation, no exporters, no network I/O; the in-memory `ToolRuntimeMetrics`
is the only metrics sink this milestone ships.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import ExecutionTrace, ToolAuditRecord

__all__ = [
    "OpenTelemetryExporter",
    "PrometheusExporter",
    "GrafanaDashboardProvider",
    "CloudLoggingSink",
    "EnterpriseSIEMSink",
    "ExternalAuditSink",
]


class OpenTelemetryExporter(ABC):
    @abstractmethod
    def export(self, trace: ExecutionTrace) -> None: ...


class PrometheusExporter(ABC):
    @abstractmethod
    def export(self, snapshot: dict[str, object]) -> None: ...


class GrafanaDashboardProvider(ABC):
    @abstractmethod
    def dashboard_url(self) -> str: ...


class CloudLoggingSink(ABC):
    @abstractmethod
    def log(self, record: ToolAuditRecord) -> None: ...


class EnterpriseSIEMSink(ABC):
    @abstractmethod
    def emit(self, record: ToolAuditRecord) -> None: ...


class ExternalAuditSink(ABC):
    @abstractmethod
    def record(self, record: ToolAuditRecord) -> None: ...
