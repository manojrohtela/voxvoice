"""Knowledge Cache (M5.2B-2) — hot-chunk LRU behind the repository layer.

A deterministic in-process LRU that fronts the chunk store for hot chunks. It lives
**inside the repository layer**: the `ChunkRepository` reads/writes through it, and the
Knowledge Runtime never sees it — so swapping in a Redis-backed `CacheBackend` later
changes nothing above the repository (read-through + write-through are orchestrated by
the repository; this class is pure storage + metrics).

Deterministic: plain `OrderedDict` LRU, no timers, no randomness. Values are cached as
storage documents (dicts), not live objects, so a networked backend serializes the exact
same shape.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    invalidations: int = 0


@runtime_checkable
class CacheBackend(Protocol):
    """The storage seam. An in-process LRU ships now; Redis implements this later."""

    def get(self, key: str) -> Mapping[str, Any] | None: ...
    def put(self, key: str, value: Mapping[str, Any]) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def __len__(self) -> int: ...


class LRUCacheBackend:
    """Bounded, deterministic LRU over an OrderedDict. `maxsize <= 0` → disabled."""

    def __init__(self, maxsize: int = 512) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.evictions = 0

    def get(self, key: str) -> Mapping[str, Any] | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return dict(self._data[key])

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        if self._maxsize <= 0:
            return
        self._data[key] = dict(value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)
            self.evictions += 1

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class KnowledgeCache:
    """Id-keyed hot-chunk cache with hit/miss/eviction/invalidation accounting.
    Read-through and write-through are orchestrated by the repository; this class is pure
    storage + metrics."""

    def __init__(self, *, backend: CacheBackend | None = None, maxsize: int = 512) -> None:
        self._backend: CacheBackend = backend or LRUCacheBackend(maxsize=maxsize)
        self.stats = CacheStats()

    def get(self, key: str) -> Mapping[str, Any] | None:
        doc = self._backend.get(key)
        if doc is None:
            self.stats.misses += 1
        else:
            self.stats.hits += 1
        return doc

    def put(self, key: str, document: Mapping[str, Any]) -> None:
        self._backend.put(key, document)
        self.stats.evictions = getattr(self._backend, "evictions", self.stats.evictions)

    def invalidate(self, key: str) -> None:
        self._backend.delete(key)
        self.stats.invalidations += 1

    def clear(self) -> None:
        self._backend.clear()

    def snapshot(self) -> dict[str, int]:
        return {
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "evictions": self.stats.evictions,
            "invalidations": self.stats.invalidations,
            "size": len(self._backend),
        }
