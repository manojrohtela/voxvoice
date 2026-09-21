"""Audio-driven endpointing on the console's real-voice path (M12.5, finding F-7).

**The defect.** `StartSessionRequest.audio_endpointing` defaults to `False`, and the frozen
`create_session` never sets it, so every product-path session has run the *segment-driven* profile:
a recognizer Final ends the turn before voice activity confirms silence. The voice-activity
`speech_end` then lands after the turn's `metric.turn` has already been published, in a second,
orphan metric. The real turn carries **no `voice_to_voice_ms`** (measured at M12.5; see
`tests/test_console_conversations.py`). This is the transition ADR-0038 TP3a anticipated and
ADR-0047 ME1 resolved: M11.8 built audio-driven endpointing, and M11.11 (R-50) made it executable.
It was simply never reached from the product.

**The fix, by composition.** `create_session` is frozen (`E10-7`), so this decorator carries the
choice across the `SessionController` seam, exactly where `RendezvousBindingController` already
sits. It is applied only to the real profile, whose composition registers the voice-activity
provider that audio endpointing requires (validation refuses the session otherwise, AR-25), and
it can be switched off with `VOXVOICE_CONSOLE_AUDIO_ENDPOINTING=false`.
"""

from __future__ import annotations

from dataclasses import replace

from core.session_control import SessionController, StartSessionRequest, StartSessionResult


class AudioEndpointingController(SessionController):
    """Starts every session with audio-driven endpointing; changes nothing else."""

    def __init__(self, inner: SessionController) -> None:
        self._inner = inner

    async def start(self, request: StartSessionRequest) -> StartSessionResult:
        return await self._inner.start(replace(request, audio_endpointing=True))

    async def end(self, session_id: str) -> None:
        await self._inner.end(session_id)

    def conversation_state(self, session_id: str) -> str | None:
        return self._inner.conversation_state(session_id)


__all__ = ["AudioEndpointingController"]
