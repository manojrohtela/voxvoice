"""Voice-activity provider implementations (capability home). M3.2 ships the
local/dev provider; real detectors (Silero, WebRTC VAD, Deepgram, OpenAI
Realtime) are drop-in additions behind the same `VoiceActivityProvider` contract."""
