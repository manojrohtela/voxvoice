"""provider.repositories — the temporary-storage anti-corruption boundary (M5.6B-1). The
only code binding the store contracts; the runtime never touches a store directly."""

from .provider_repository import ProviderRepository

__all__ = ["ProviderRepository"]
