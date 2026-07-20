"""prompt.repositories — the temporary-storage anti-corruption boundary (M5.4B-1). The only
code binding the store contracts; the runtime never touches a store directly."""

from .prompt_repository import PromptRepository

__all__ = ["PromptRepository"]
