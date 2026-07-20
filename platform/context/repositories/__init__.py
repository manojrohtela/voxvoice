"""context.repositories — the temporary-storage anti-corruption boundary (M5.3B-1). The
only code binding the store contracts; the runtime never touches a store directly."""

from .context_repository import ContextRepository

__all__ = ["ContextRepository"]
