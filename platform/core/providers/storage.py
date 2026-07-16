"""Storage capability contract — recordings and artifacts (Constitution §A6).
Interface only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StorageCapabilities:
    signed_access: bool = False
    durability_class: str = "standard"


class StorageProvider(ABC):
    name: str
    capabilities: StorageCapabilities

    @abstractmethod
    async def put(self, *, key: str, data: bytes, content_type: str) -> str:
        """Store an artifact; return a retrieval reference."""

    @abstractmethod
    async def get(self, *, key: str) -> bytes:
        """Retrieve an artifact."""
