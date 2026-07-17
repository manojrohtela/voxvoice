"""Voice-activity states and legal transitions (M3.2).

`VoiceActivityState` is the **deterministic activity model** the framework
derives from a provider's raw per-frame verdicts. Unlike `TurnPhase` (a single
pass per turn) this model is **cyclic** — one conversation contains many
silence→speech→silence cycles, so there are no terminal states.

The model is intentionally coarse and extensible: a richer engine may add finer
states (Constitution §A12 — additive extension) by extending `VA_TRANSITIONS`
without changing existing edges. `SILENCE_CONFIRMED` is the momentary
end-of-utterance marker; the engine normalizes it back to `SILENCE` before the
next cycle.

M3.2 is the framework only — the states describe *acoustic activity*, not
semantic/adaptive endpointing (M3.3+).
"""

from __future__ import annotations

from enum import StrEnum


class VoiceActivityState(StrEnum):
    SILENCE = "silence"  # no speech detected
    SPEECH_STARTING = "speech_starting"  # first speech frame(s); not yet confirmed
    SPEAKING = "speaking"  # speech confirmed (past the start threshold)
    SPEECH_ENDING = "speech_ending"  # silence after speech; not yet confirmed
    SILENCE_CONFIRMED = "silence_confirmed"  # end-of-utterance confirmed (momentary)


# Legal activity transitions (source -> allowed targets). Cyclic — no terminals.
# SPEECH_STARTING may fall back to SILENCE (a false start); SPEECH_ENDING may
# return to SPEAKING (speech resumed before the end was confirmed).
VA_TRANSITIONS: dict[VoiceActivityState, frozenset[VoiceActivityState]] = {
    VoiceActivityState.SILENCE: frozenset({VoiceActivityState.SPEECH_STARTING}),
    VoiceActivityState.SPEECH_STARTING: frozenset(
        {VoiceActivityState.SPEAKING, VoiceActivityState.SILENCE}
    ),
    VoiceActivityState.SPEAKING: frozenset({VoiceActivityState.SPEECH_ENDING}),
    VoiceActivityState.SPEECH_ENDING: frozenset(
        {VoiceActivityState.SPEAKING, VoiceActivityState.SILENCE_CONFIRMED}
    ),
    VoiceActivityState.SILENCE_CONFIRMED: frozenset({VoiceActivityState.SILENCE}),
}
