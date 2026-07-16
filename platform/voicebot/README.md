# VoxVoice — local voice agent (Pipecat MVP)

A minimal, fully-local voice agent to validate the conversation before we put it
behind VoxVoice's `EngineAdapter` and deploy. **No Oracle, no TURN, no Docker** —
runs natively on your Mac; localhost WebRTC connects directly.

**Pipeline:** browser mic → **Groq Whisper** (STT) → **Groq Llama-3.3** (LLM) →
**Cartesia** (TTS) → browser speaker. Turn-taking/barge-in via Silero VAD.

## Run

```bash
cd VoxVoice/platform/voicebot
./.venv/bin/python bot.py
```

Then open **http://localhost:7860**, **Allow microphone**, click **Connect**, and
talk. The agent greets you first.

## Config

Edit `.env` (already populated with your Groq + Cartesia keys; gitignored):

| Var | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | (set) | STT + LLM |
| `CARTESIA_API_KEY` | (set) | TTS |
| `GROQ_STT_MODEL` | `whisper-large-v3-turbo` | |
| `GROQ_LLM_MODEL` | `llama-3.3-70b-versatile` | |
| `CARTESIA_VOICE_ID` | a default English voice | swap for another Cartesia voice |
| `VOICEBOT_SYSTEM_PROMPT` | friendly assistant | set per use-case |

## Notes / next steps

- A harmless macOS warning about duplicate `libavdevice` (av vs opencv) may print;
  ignore it. If it ever crashes audio, `./.venv/bin/pip uninstall -y opencv-python`.
- **Hinglish moat:** swap `GroqSTTService` → a Sarvam STT/TTS service once you have
  a Sarvam key (the whole point vs Vapi).
- **Productionize:** wrap this pipeline in a `PipecatAdapter` (implements
  `adapters/base.py`) so the Platform API can start/stop it like any engine.
- **Remote users (later):** deploy on a public-IP box + a small coturn, or use
  Pipecat's Daily transport (free tier) so you don't self-host TURN.
