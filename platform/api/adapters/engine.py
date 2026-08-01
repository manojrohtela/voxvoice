"""Provider execution engine (M8.14).

The real `ExecutionEngine` (the Foundation seam): it takes the runtime's resolved plan + a user
turn and executes it through provider adapters — **no planning here** (golden rule). It selects
the LLM/voice provider from the agent's published config, runs the LLM under resilience
(retry/timeout/circuit-breaker/rate-limit), executes each enabled tool step under the tool's own
timeout/retry, pulls multi-turn context from the durable memory adapter, and assembles an
`ExecutionResult` (assistant response + tool results + provider metadata + token usage + latency +
errors + diagnostics). Streaming yields token/tool/voice events with cancellation + backpressure.
"""

from __future__ import annotations

from collections.abc import Iterable

from api.foundation import (
    ExecutionEngine,
    ExecutionResult,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)

from .llm import build_llm_adapter
from .memory import DurableMemoryAdapter
from .resilience import (
    CircuitBreaker,
    ExecutionCancelledError,
    ProviderUnavailableError,
    RateLimiter,
    RetrySpec,
    execute_with_resilience,
)
from .tools import ToolExecutorDispatcher
from .transport import ProviderRequest, Transport
from .voice import build_voice_adapter


class ProviderExecutionEngine(ExecutionEngine):
    def __init__(
        self,
        transport: Transport,
        *,
        memory: DurableMemoryAdapter | None = None,
        tools: ToolExecutorDispatcher,
        api_keys: dict[str, str] | None = None,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._transport = transport
        self._memory = memory
        self._tools = tools
        self._api_keys = api_keys or {}
        self._breaker = breaker or CircuitBreaker()
        self._limiter = limiter

    # ── prompt assembly (context only — no planning) ──────────────────────────────
    def _prompt(self, state, user_input: str, options: dict) -> str:
        history = ""
        if self._memory is not None and options.get("session_id"):
            prior = self._memory.history(options.get("org_id", ""), options["session_id"])
            history = "\n".join(f"{m['role']}: {m['content']}" for m in prior)
        head = f"{history}\n\n" if history else ""
        return f"{head}{state.resolved_prompt.text}\n\nUSER: {user_input}"

    def execute(self, state, user_input, *, options) -> ExecutionResult:
        model = options.get("model", {})
        provider = model.get("provider", "")
        errors: list[dict] = []
        llm = build_llm_adapter(
            provider, self._transport, model=model.get("model", ""),
            api_key=self._api_keys.get(provider, ""),
        )
        prompt = self._prompt(state, user_input, options)
        out = execute_with_resilience(
            lambda: llm.complete(prompt, options=model),
            timeout_ms=int(options.get("timeout_ms", 30_000)),
            retry=RetrySpec(max_retries=int(options.get("max_retries", 2))),
            breaker=self._breaker, limiter=self._limiter,
            latency_of=lambda r: int(r.get("latency_ms", 0)),
        )
        assistant = out["text"]
        usage = out["token_usage"]
        tool_results = self._run_tools(state, user_input, options)
        errors.extend(
            {"code": "tool_failed", "message": tr.get("error", ""), "provider": provider}
            for tr in tool_results if tr.get("status") == "error"
        )
        provider_metadata = {"provider": provider, "model": out.get("model", "")}
        voice_cfg = options.get("voice", {})
        if voice_cfg.get("enabled") and voice_cfg.get("provider"):
            voice = build_voice_adapter(
                voice_cfg["provider"], self._transport,
                voice=voice_cfg.get("voice_name", ""),
                api_key=self._api_keys.get(voice_cfg["provider"], ""),
            )
            provider_metadata["voice"] = voice.synthesize(assistant, options=voice_cfg)
        messages = (
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant},
        )
        return ExecutionResult(
            assistant_response=assistant, messages=messages, tool_results=tuple(tool_results),
            provider_metadata=provider_metadata,
            token_usage=TokenUsage(usage["input_tokens"], usage["output_tokens"]),
            latency_ms=out["latency_ms"], errors=tuple(errors),
            diagnostics=state.diagnostics,
            execution_metadata={"engine": "provider", "provider": provider, "streamed": False},
        )

    def _run_tools(self, state, user_input: str, options: dict) -> list[dict]:
        # Thread org/project context so scoped tools (e.g. the Knowledge Tool, M8.15) can
        # enforce isolation + resolve their collection. Non-scoped tools ignore the extra keys.
        arguments = {
            "input": user_input,
            "org_id": options.get("org_id", ""),
            "project_id": options.get("project_id", ""),
        }
        results = []
        for step in state.tool_plan.steps:
            if not step.enabled:
                continue
            results.append(self._tools.invoke_step(step.as_dict(), arguments=arguments))
        return results

    # ── streaming (tokens/tools/voice + cancellation + backpressure) ──────────────
    def stream(self, state, user_input, *, options) -> Iterable[StreamEvent]:
        model = options.get("model", {})
        provider = model.get("provider", "")
        cancel = options.get("cancel")
        max_events = int(options.get("max_events", 10_000))  # backpressure cap
        prompt = self._prompt(state, user_input, options)
        req = ProviderRequest(
            provider=provider, method="POST", url="stream", body={"prompt": prompt, **model}
        )
        seq = 0
        text_parts: list[str] = []
        in_tok = out_tok = latency = 0
        try:
            for chunk in self._transport.stream(req):
                if cancel is not None and getattr(cancel, "cancelled", False):
                    raise ExecutionCancelledError("stream cancelled")
                if seq >= max_events:
                    break  # backpressure
                if chunk.get("type") == "token":
                    text_parts.append(chunk.get("text", ""))
                    yield StreamEvent(seq, StreamEventType.TOKEN, {"text": chunk.get("text", "")})
                    seq += 1
                elif chunk.get("type") == "done":
                    usage = chunk.get("usage", {})
                    in_tok, out_tok = int(usage.get("input", 0)), int(usage.get("output", 0))
                    latency = int(chunk.get("latency_ms", 0))
        except ProviderUnavailableError as exc:
            yield StreamEvent(seq, StreamEventType.ERROR, {"message": str(exc)})
            return
        assistant = "".join(text_parts).strip()
        tool_results = self._run_tools(state, user_input, options)
        for tr in tool_results:
            yield StreamEvent(seq, StreamEventType.TOOL, tr)
            seq += 1
        voice_cfg = options.get("voice", {})
        if voice_cfg.get("enabled") and voice_cfg.get("provider"):
            yield StreamEvent(seq, StreamEventType.VOICE, {"chars": len(assistant)})
            seq += 1
        result = ExecutionResult(
            assistant_response=assistant,
            messages=(
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": assistant},
            ),
            tool_results=tuple(tool_results),
            provider_metadata={"provider": provider, "model": model.get("model", "")},
            token_usage=TokenUsage(in_tok, out_tok), latency_ms=latency,
            diagnostics=state.diagnostics,
            execution_metadata={"engine": "provider", "provider": provider, "streamed": True},
        )
        yield StreamEvent(seq, StreamEventType.DONE, {"result": result.as_dict()})

    def describe_providers(self, options: dict) -> dict:
        model = options.get("model", {})
        voice = options.get("voice", {})
        return {
            "llm": {"provider": model.get("provider", ""), "model": model.get("model", "")},
            "voice": {
                "enabled": bool(voice.get("enabled")), "provider": voice.get("provider", ""),
            },
            "circuit_breaker_open": self._breaker.opened,
            "failures": self._breaker.failures,
        }
