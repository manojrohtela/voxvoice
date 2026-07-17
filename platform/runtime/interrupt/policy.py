"""InterruptPolicy — the configurable rules that shape interruption decisions (M3.4).

Nothing about barge-in is hardcoded. Every knob lives here and is injected into the
`InterruptEngine`. Durations are expressed as *observation frames* (deterministic,
unit-testable); one frame maps to one provider observation (a Voice Activity sample
/ speech signal). A real streaming profile binds a frame to a wall-clock ms via the
provider's frame size — kept out of the deterministic core on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterruptPolicy:
    """Rules for when caller speech during assistant playback becomes an interrupt.

    Defaults are conservative but immediate: after the grace window, the first
    qualifying speech observation confirms a barge-in (`min_interrupt_speech_frames=1`,
    `interrupt_hold_frames=0`), which is the natural behaviour for a provider that
    emits a confirmed `SPEAKING` boundary. Noisier detectors raise the thresholds.
    """

    min_interrupt_speech_frames: int = 1  # sustained speech obs (after grace) to raise a potential
    confidence_threshold: float = 0.0  # minimum confidence to accept an interrupt speech signal
    interrupt_hold_frames: int = 0  # extra speech obs to hold a potential before confirming
    assistant_grace_frames: int = 0  # leading speech obs after assistant start that never interrupt
    duplicate_suppression: bool = True  # collapse repeat confirms within one assistant utterance
