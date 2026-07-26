"""integration.http — deterministic IntegrationError/PlatformError → HTTP
translation (M6.7B-2).

Per the M6.7A design §12: Integration's only new behaviour at the API boundary
is translating a capability's own typed error into an HTTP status the existing
`api/` layer already knows how to emit — never a new retry, never a new
validation pass. This package produces a **framework-neutral**
`HttpErrorResponse` (status code + typed body); it imports **no** `api`/FastAPI
symbol (that would violate the dependency rules — `api` is not an allowed
`integration` dependency). The `api/` layer consumes the result and raises its
own `HTTPException`. No capability-specific logic lives here — mapping is by the
error's own already-typed category (`errors.failure.classify_error`).
"""

from .translate import HttpErrorResponse, translate_error

__all__ = ["HttpErrorResponse", "translate_error"]
