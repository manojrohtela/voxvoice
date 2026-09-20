"""runtime.pipeline.audio — the Runtime's sample-rate normalization boundary (M11.2).

Owns the **two** conversion points AR-41 permits: one at media ingress and one at
media egress, both invoked by `RuntimePipeline`. Nothing else in the platform
converts a frame's rate — `core/` holds no behaviour, and providers are told
plainly in `core/audio.py` that they do not resample.

Ratified by **ADR-0044** AD3/AD4
(`docs/adr/ADR-0044-audio-representation-resampling-and-media-buffers.md`).
Dependency-free by design: see `resampler.py` on why `audioop` is not available
and why no library is introduced here.
"""

from .resampler import AudioNormalizer

__all__ = ["AudioNormalizer"]
