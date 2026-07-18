"""MetadataRepository — small key/value state over a `MetadataStore` (M5.1B-1).

Cursors, counters, and per-tier config — the bookkeeping the runtime keeps beside the
records themselves. Thin typed helpers over the string k/v contract; deterministic.
Depends only on the `MetadataStore` contract.
"""

from __future__ import annotations

from core.providers.stores import MetadataStore


class MetadataRepository:
    def __init__(self, store: MetadataStore) -> None:
        self._store = store

    async def get(self, key: str) -> str | None:
        return await self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        await self._store.set(key, value)

    async def delete(self, key: str) -> None:
        await self._store.delete(key)

    # ── typed convenience over the string contract ───────────────────────────
    async def get_int(self, key: str, default: int = 0) -> int:
        raw = await self._store.get(key)
        return int(raw) if raw is not None else default

    async def incr(self, key: str, n: int = 1) -> int:
        value = await self.get_int(key) + n
        await self._store.set(key, str(value))
        return value
