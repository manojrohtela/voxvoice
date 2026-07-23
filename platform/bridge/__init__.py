"""bridge — composition-root modules wiring two closed Epic 5 runtimes together without
either importing the other. M5.6B-2 adds `ProviderModelBridge` (provider ⇄ reasoning); future
bridges (e.g. Tool ⇄ Reasoning) land here the same way. Neither `provider` nor `reasoning`
imports this package — only a composition root imports `bridge`."""

from .provider_model_bridge import ProviderModelBridge

__all__ = ["ProviderModelBridge"]
