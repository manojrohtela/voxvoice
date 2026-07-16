"""
VoxVoice — minimal local voice agent (Pipecat).

Pipeline: SmallWebRTC (browser mic/speaker) → Groq Whisper STT → Groq LLM →
Cartesia TTS → back to the browser. Runs natively on your Mac via Pipecat's
dev runner, so localhost WebRTC connects with NO TURN server / no Oracle.

Run:
    ./.venv/bin/python bot.py
then open the URL it prints (http://localhost:7860) and click Connect.

This is the "engine" we'll later put behind VoxVoice's EngineAdapter once the
conversation feels good. Swap Groq STT → Sarvam later for the Hinglish moat.
"""

import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMRunFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.runner.run import main
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.transports.base_transport import TransportParams

load_dotenv()

SYSTEM_PROMPT = os.getenv(
    "VOICEBOT_SYSTEM_PROMPT",
    "You are a warm, concise voice assistant for AgentHive. "
    "Speak naturally and keep replies short — this is a spoken conversation, "
    "not an essay. Greet the user first, briefly, and ask how you can help. "
    "Avoid markdown, bullet points, or emojis; just talk.",
)

class DebugLogger(FrameProcessor):
    """Logs VAD + transcription so we can see each turn the bot actually hears."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            logger.info("🎙️  user started speaking (VAD fired)")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            logger.info("🤫 user stopped speaking")
        elif isinstance(frame, TranscriptionFrame):
            logger.info(f"👤 STT heard: {frame.text!r}")
        await self.push_frame(frame, direction)


# Browser (SmallWebRTC) transport — audio in/out + voice-activity detection for
# natural turn-taking and barge-in.
transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        # Force 16 kHz on the input: Silero VAD only supports 8k/16k, and the
        # WebRTC-native 48 kHz made it detect nothing. Also lower min_volume
        # (default 0.6 was above the mic level and discarded all speech).
        audio_in_sample_rate=16000,
        vad_analyzer=SileroVADAnalyzer(
            sample_rate=16000,
            params=VADParams(confidence=0.5, start_secs=0.2, stop_secs=0.6, min_volume=0.05),
        ),
    ),
}


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)

    stt = GroqSTTService(
        api_key=os.getenv("GROQ_API_KEY"),
        model=os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
    )
    llm = GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        model=os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
    )
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id=os.getenv("CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121"),
    )

    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}])
    aggregators = LLMContextAggregatorPair(context)

    pipeline = Pipeline([
        transport.input(),
        stt,
        DebugLogger(),
        aggregators.user(),
        llm,
        tts,
        transport.output(),
        aggregators.assistant(),
    ])

    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        logger.info("Client connected — kicking off the greeting")
        # Run the LLM once with just the system prompt so the agent speaks first.
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):
        logger.info("Client disconnected — ending session")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


if __name__ == "__main__":
    main()
