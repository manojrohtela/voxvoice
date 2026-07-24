"""Enterprise governance extension points (M6.1B-2) — interfaces only.

Where real enterprise integrations plug in later, without ever touching
`policies.py`/`approval.py`/`authorization.py`/`tenancy.py`. No implementation —
neutral interfaces only, mirroring `tool.extensions`'s own "seam, not a build"
posture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain import ToolRequest
from ..extensions import PolicyVerdict
from .domain import ApprovalState

__all__ = [
    "EnterprisePolicyProvider",
    "ExternalApprovalProvider",
    "CloudSecretManager",
    "VaultProvider",
    "KMSProvider",
    "OIDCProvider",
    "SSOProvider",
    "RBACProvider",
    "ABACProvider",
]


class EnterprisePolicyProvider(ABC):
    """A remote/central policy decision point (OPA-style) that could supersede
    or augment `ToolPolicyEngine`'s local rule evaluation."""

    @abstractmethod
    def evaluate(self, request: ToolRequest) -> PolicyVerdict: ...


class ExternalApprovalProvider(ABC):
    """Where a human-workflow tool (Slack/email/ticketing) resolves a `PENDING`
    approval instead of `ToolApprovalEngine`'s offline in-memory bookkeeping."""

    @abstractmethod
    def status_for(self, approval_id: str) -> ApprovalState: ...


class CloudSecretManager(ABC):
    """A managed cloud secret store (distinct from `Vault`/`KMS` below by
    vendor shape, e.g. AWS/GCP/Azure secret managers)."""

    @abstractmethod
    def resolve(self, key: str) -> str: ...


class VaultProvider(ABC):
    @abstractmethod
    def resolve(self, path: str) -> str: ...


class KMSProvider(ABC):
    @abstractmethod
    def decrypt(self, ciphertext: bytes) -> bytes: ...


class OIDCProvider(ABC):
    @abstractmethod
    def verify(self, token: str) -> str: ...  # -> subject


class SSOProvider(ABC):
    @abstractmethod
    def resolve_session(self, token: str) -> str: ...  # -> identity id


class RBACProvider(ABC):
    @abstractmethod
    def roles_for(self, identity_id: str) -> frozenset[str]: ...


class ABACProvider(ABC):
    @abstractmethod
    def attributes_for(self, identity_id: str) -> dict[str, str]: ...
