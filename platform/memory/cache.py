"""Memory Cache (M5.1B-2) — hot-object LRU behind the repository layer.

A deterministic in-process LRU that fronts the document store for hot records (working
memory, recently-accessed objects). It lives **inside the repository layer**: the
`MemoryRepository` reads/writes through it, and the Memory Runtime never sees it — so
swapping in a Redis-backed `CacheBackend` later changes nothing above the repository.

Deterministic: plain `OrderedDict` LRU, no timers, no randomness. Values are cached as
storage documents (dicts), not live objects, so a networked backend can serialize the
exact same shape.
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

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.evictions = 0

    def get(self, key: str) -> Mapping[str, Any] | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)  # mark most-recently-used
        return dict(self._data[key])

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        if self._maxsize <= 0:
            return
        self._data[key] = dict(value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)  # evict least-recently-used
            self.evictions += 1

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class MemoryCache:
    """Id-keyed hot-record cache with hit/miss/eviction accounting. Read-through and
    write-through is orchestrated by the repository; this class is pure storage +
    metrics."""

    def __init__(self, *, backend: CacheBackend | None = None, maxsize: int = 256) -> None:
        self._backend: CacheBackend = backend or LRUCacheBackend(maxsize=maxsize)
        self.stats = CacheStats()

    def get(self, memory_id: str) -> Mapping[str, Any] | None:
        doc = self._backend.get(memory_id)
        if doc is None:
            self.stats.misses += 1
        else:
            self.stats.hits += 1
        return doc

    def put(self, memory_id: str, document: Mapping[str, Any]) -> None:
        self._backend.put(memory_id, document)
        # surface backend evictions if the backend exposes them
        self.stats.evictions = getattr(self._backend, "evictions", self.stats.evictions)

    def invalidate(self, memory_id: str) -> None:
        self._backend.delete(memory_id)
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
