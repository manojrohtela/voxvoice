"""Secrets abstraction (M6.1B-2).

Reuses the frozen `tool.extensions.SecretsManager` seam directly rather than
defining a parallel interface — this milestone's `EchoSecretsProvider` is simply
its first concrete (deterministic, offline) implementation. No real secret
storage: cloud secret managers (Vault/KMS/etc.) are reserved, interface-only,
extension points (`extensions.py`).
"""

from __future__ import annotations

from ..extensions import SecretsManager
from ..ids import content_hash


class EchoSecretsProvider(SecretsManager):
    """Deterministic dev/test default. Never echoes a caller-supplied value back —
    there is none; it derives a fixed-shape, content-hashed placeholder token so
    tests can assert determinism without ever handling a real credential."""

    def resolve(self, key: str) -> str:
        return "echo_secret_" + content_hash(key)
