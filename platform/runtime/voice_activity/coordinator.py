"""VoiceActivityCoordinator — the Runtime Pipeline's integration point for voice
activity (M3.2).

A thin façade the pipeline calls with inbound audio frames. It runs the
`VoiceActivityProvider`, feeds the verdict to the `VoiceActivityEngine`, and — as
the framework's one link to Turn Intelligence — notifies the Turn Engine when
speech is confirmed. It NEVER owns turns; it supplies speech-activity signals
only (the Turn Engine remains the sole writer of turn state).

Boundary: keeping the pipeline coupled to this small surface (`start` / `observe`
/ `stop`) is what lets future VAD engines plug in without touching the pipeline.
The pipeline calls `observe` as a pure notification interleaved with the existing
flow — it analyzes a copy of each frame and never alters, drops, or delays the
audio the STT stage receives (conversation behaviour unchanged).

Failure policy (DL-0013): voice activity is auxiliary. A provider `start`/`detect`
failure raises a typed `VoiceActivityProviderError`; the pipeline degrades
gracefully (logs, continues without VAD) rather than breaking the call. State
transitions themselves stay strict (the engine raises on any illegal move).
"""

from __future__ import annotations

import logging

from ..errors import VoiceActivityProviderError
from .engine import VoiceActivityEngine
from .state import VoiceActivityState

log = logging.getLogger("voxvoice.runtime.voice_activity")


class VoiceActivityCoordinator:
    def __init__(
        self,
        *,
        provider,
        engine: VoiceActivityEngine,
        session_id: str,
        turns=None,
        endpoint=None,
    ) -> None:
        self._provider = provider
        self._engine = engine
        self._session_id = session_id
        # The Turn Engine façade (optional). The VAF notifies it of speech onset;
        # it never manipulates turn state directly.
        self._turns = turns
        # The Endpoint Intelligence façade (optional, M3.3). The VAF supplies
        # speech/silence observations to enrich endpoint decisions; it never decides
        # completion itself (the Turn Engine stays the sole owner of turn state).
        self._endpoint = endpoint

    @property
    def engine(self) -> VoiceActivityEngine:
        return self._engine

    async def start(self) -> None:
        """Prepare the provider for this session. Raises VoiceActivityProviderError
        on failure (the caller decides whether to degrade)."""
        try:
            await self._provider.start(session_id=self._session_id)
        except Exception as exc:  # noqa: BLE001 — wrapped as a typed capability error
            raise VoiceActivityProviderError(
                f"voice-activity provider '{getattr(self._provider, 'name', '?')}' "
                f"failed to start: {exc}"
            ) from exc

    def observe(self, frame) -> VoiceActivityState | None:
        """Classify one inbound audio frame and advance the activity model. On a
        confirmed speech onset, notify the Turn Engine (speech-activity signal
        only). Returns the resulting state on a net change, else None. Raises
        VoiceActivityProviderError if the provider's `detect` fails."""
        try:
            sample = self._provider.detect(frame, session_id=self._session_id)
        except Exception as exc:  # noqa: BLE001 — wrapped as a typed capability error
            raise VoiceActivityProviderError(
                f"voice-activity provider '{getattr(self._provider, 'name', '?')}' "
                f"detect failed: {exc}"
            ) from exc
        changed = self._engine.observe(sample)
        if changed is VoiceActivityState.SPEAKING:
            # Speech confirmed → the caller's turn has begun. `on_user_speech` is
            # idempotent, so this composes safely with STT-driven turn onset.
            if self._turns is not None:
                self._turns.on_user_speech()
            if self._endpoint is not None:
                self._endpoint.on_user_speech()  # enrich the endpoint's speech run
        elif changed is VoiceActivityState.SILENCE_CONFIRMED and self._endpoint is not None:
            # Confirmed end-of-speech → let the endpoint framework count it toward a
            # pending endpoint's silence hold (never decides completion on its own).
            self._endpoint.on_user_silence()
        return changed

    async def stop(self) -> None:
        """Release the provider's session resources. Best-effort — a shutdown
        failure is logged, never raised (the session is already ending)."""
        try:
            await self._provider.stop(session_id=self._session_id)
        except Exception as exc:  # noqa: BLE001 — shutdown is best-effort
            log.warning(
                "voice-activity provider '%s' stop failed (ignored): %s",
                getattr(self._provider, "name", "?"),
                exc,
            )
