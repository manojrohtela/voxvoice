"""Analytics capability contract — an optional metric sink (Constitution §A6).
Interface only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.events import Event


@dataclass(frozen=True, slots=True)
class AnalyticsCapabilities:
    delivery: str = "at_least_once"


class AnalyticsProvider(ABC):
    name: str
    capabilities: AnalyticsCapabilities

    @abstractmethod
    async def consume(self, event: Event) -> None:
        """Receive one metric/analytics event (optional sink)."""
