"""integration.errors — the one boundary error type plus Bundle 2 failure
aggregation/translation.

`IntegrationError` (Bundle 1, design §12) is unchanged — re-exported here so
every existing `from ..errors import IntegrationError` / `from .errors import
IntegrationError` import keeps working after the module became a package
(M6.7B-2 scope requires an `integration/errors/` package). Bundle 2 adds
failure *propagation* and *aggregation* only — it never replaces a capability's
own typed error model (design §12): capability errors propagate verbatim; only
a composition-root boundary condition raises `IntegrationError`.
"""

from .base import IntegrationError
from .failure import FailureTranslator, IntegrationFailurePolicy, PlatformFailureSummary

__all__ = [
    "IntegrationError",
    "IntegrationFailurePolicy",
    "FailureTranslator",
    "PlatformFailureSummary",
]
