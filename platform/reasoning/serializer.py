"""Reasoning serialization (M5.5B-1).

Deterministic, provider-neutral translation between the domain aggregates (`InferenceRequest`,
`ResponseChunk`, `ResponsePackage`, `ReasoningSession`) and the plain dict shapes used by
storage, events, and the future API. Round-trippable; no I/O. Stateless — safe to share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    CancellationRecord,
    ChunkKind,
    FinishReason,
    GenerationParameters,
    InferenceMessage,
    InferenceRequest,
    InferenceState,
    ReasoningLineage,
    ReasoningMetadata,
    ReasoningScope,
    ReasoningSession,
    ResponseChunk,
    ResponseMetadata,
    ResponsePackage,
    RetryRecord,
    StreamingMetadata,
    TokenUsage,
)


def _scope_to_dict(s: ReasoningScope) -> dict[str, Any]:
    return {"org_id": s.org_id, "conversation_id": s.conversation_id,
            "session_id": s.session_id, "user_id": s.user_id}


def _scope_from_dict(d: Mapping[str, Any]) -> ReasoningScope:
    return ReasoningScope(org_id=d.get("org_id"), conversation_id=d.get("conversation_id"),
                          session_id=d.get("session_id"), user_id=d.get("user_id"))


def _gen_to_dict(g: GenerationParameters) -> dict[str, Any]:
    return {"temperature": g.temperature, "max_tokens": g.max_tokens, "top_p": g.top_p,
            "stop": list(g.stop), "seed": g.seed, "extra": dict(g.extra)}


def _gen_from_dict(d: Mapping[str, Any]) -> GenerationParameters:
    return GenerationParameters(
        temperature=d.get("temperature"), max_tokens=d.get("max_tokens"), top_p=d.get("top_p"),
        stop=tuple(d.get("stop", ())), seed=d.get("seed"), extra=dict(d.get("extra", {})),
    )


def _stream_to_dict(s: StreamingMetadata) -> dict[str, Any]:
    return {"streamed": s.streamed, "chunk_count": s.chunk_count,
            "first_chunk_index": s.first_chunk_index}


def _stream_from_dict(d: Mapping[str, Any]) -> StreamingMetadata:
    return StreamingMetadata(streamed=d.get("streamed", False), chunk_count=d.get("chunk_count", 0),
                             first_chunk_index=d.get("first_chunk_index", 0))


class ReasoningSerializer:
    """Stateless, deterministic (de)serializer for reasoning aggregates."""

    # ── ResponseChunk ────────────────────────────────────────────────────────
    def chunk_to_dict(self, c: ResponseChunk) -> dict[str, Any]:
        return {"id": c.id, "index": c.index, "delta": c.delta, "kind": c.kind.value,
                "done": c.done, "metadata": dict(c.metadata)}

    def chunk_from_dict(self, d: Mapping[str, Any]) -> ResponseChunk:
        return ResponseChunk(id=d["id"], index=d["index"], delta=d["delta"],
                             kind=ChunkKind(d.get("kind", ChunkKind.TOKEN.value)),
                             done=d.get("done", False), metadata=dict(d.get("metadata", {})))

    # ── InferenceRequest ─────────────────────────────────────────────────────
    def request_to_dict(self, r: InferenceRequest) -> dict[str, Any]:
        m = r.metadata
        return {
            "session_id": r.session_id,
            "messages": [{"role": x.role, "content": x.content, "metadata": dict(x.metadata)}
                         for x in r.messages],
            "provider": r.provider, "model": r.model,
            "generation": _gen_to_dict(r.generation),
            "prompt_hash": r.prompt_hash, "prompt_fingerprint": r.prompt_fingerprint,
            "streaming": r.streaming, "timeout_s": r.timeout_s,
            "metadata": {"request": dict(m.request), "conversation": dict(m.conversation),
                         "runtime": dict(m.runtime)},
            "cancellation_token": r.cancellation_token,
            "request_hash": r.request_hash, "version": r.version,
            "scope": _scope_to_dict(r.scope),
        }

    def request_from_dict(self, d: Mapping[str, Any]) -> InferenceRequest:
        m = d.get("metadata", {})
        return InferenceRequest(
            session_id=d["session_id"],
            messages=tuple(
                InferenceMessage(role=x["role"], content=x["content"],
                                 metadata=dict(x.get("metadata", {})))
                for x in d.get("messages", ())
            ),
            provider=d.get("provider", "echo"), model=d.get("model", "default"),
            generation=_gen_from_dict(d.get("generation", {})),
            prompt_hash=d.get("prompt_hash", ""),
            prompt_fingerprint=d.get("prompt_fingerprint", ""),
            streaming=d.get("streaming", True), timeout_s=d.get("timeout_s"),
            metadata=ReasoningMetadata(request=dict(m.get("request", {})),
                                       conversation=dict(m.get("conversation", {})),
                                       runtime=dict(m.get("runtime", {}))),
            cancellation_token=d.get("cancellation_token"),
            request_hash=d.get("request_hash", ""), version=d.get("version", "1"),
            scope=_scope_from_dict(d.get("scope", {})),
        )

    # ── ResponsePackage ──────────────────────────────────────────────────────
    def package_to_document(self, pkg: ResponsePackage) -> dict[str, Any]:
        ln = pkg.lineage
        return {
            "id": pkg.id, "session_id": pkg.session_id, "scope": _scope_to_dict(pkg.scope),
            "state": pkg.state.value, "text": pkg.text,
            "chunks": [self.chunk_to_dict(c) for c in pkg.chunks],
            "tool_decisions": [dict(x) for x in pkg.tool_decisions],
            "usage": {"prompt_tokens": pkg.usage.prompt_tokens,
                      "completion_tokens": pkg.usage.completion_tokens},
            "finish_reason": pkg.finish_reason.value,
            "streaming": _stream_to_dict(pkg.streaming),
            "retry": [{"attempt": r.attempt, "reason": r.reason, "delay_s": r.delay_s, "at": r.at}
                      for r in pkg.retry],
            "cancellation": ({"reason": pkg.cancellation.reason, "at": pkg.cancellation.at}
                             if pkg.cancellation is not None else None),
            "timing": dict(pkg.timing),
            "metadata": {"provider_metadata": dict(pkg.metadata.provider_metadata),
                         "model_metadata": dict(pkg.metadata.model_metadata),
                         "completion_metadata": dict(pkg.metadata.completion_metadata)},
            "lineage": {
                "prompt_hash": ln.prompt_hash, "prompt_fingerprint": ln.prompt_fingerprint,
                "inference_request_hash": ln.inference_request_hash,
                "provider": ln.provider, "model": ln.model,
                "retry_history": [{"attempt": r.attempt, "reason": r.reason,
                                   "delay_s": r.delay_s, "at": r.at} for r in ln.retry_history],
                "tool_history": [dict(x) for x in ln.tool_history],
                "streaming": _stream_to_dict(ln.streaming),
                "transformation": ln.transformation, "response_hash": ln.response_hash,
            },
            "quality": dict(pkg.quality),
            "provider": pkg.provider, "model": pkg.model,
            "request_hash": pkg.request_hash, "response_hash": pkg.response_hash,
            "response_version": pkg.response_version,
            "created_at": pkg.created_at, "expires_at": pkg.expires_at,
            "org_id": pkg.org_id, "conversation_id": pkg.conversation_id,
        }

    def package_from_document(self, d: Mapping[str, Any]) -> ResponsePackage:
        u = d.get("usage", {})
        m = d.get("metadata", {})
        ln = d.get("lineage", {})
        c = d.get("cancellation")
        return ResponsePackage(
            id=d["id"], session_id=d.get("session_id", ""),
            scope=_scope_from_dict(d.get("scope", {})),
            state=InferenceState(d.get("state", InferenceState.REQUESTED.value)),
            text=d.get("text", ""),
            chunks=tuple(self.chunk_from_dict(x) for x in d.get("chunks", ())),
            tool_decisions=tuple(dict(x) for x in d.get("tool_decisions", ())),
            usage=TokenUsage(prompt_tokens=u.get("prompt_tokens", 0),
                             completion_tokens=u.get("completion_tokens", 0)),
            finish_reason=FinishReason(d.get("finish_reason", FinishReason.STOP.value)),
            streaming=_stream_from_dict(d.get("streaming", {})),
            retry=tuple(RetryRecord(attempt=r["attempt"], reason=r["reason"],
                                    delay_s=r.get("delay_s", 0.0), at=r.get("at", 0.0))
                        for r in d.get("retry", ())),
            cancellation=(CancellationRecord(reason=c["reason"], at=c.get("at", 0.0))
                          if c is not None else None),
            timing=dict(d.get("timing", {})),
            metadata=ResponseMetadata(
                provider_metadata=dict(m.get("provider_metadata", {})),
                model_metadata=dict(m.get("model_metadata", {})),
                completion_metadata=dict(m.get("completion_metadata", {}))),
            lineage=ReasoningLineage(
                prompt_hash=ln.get("prompt_hash", ""),
                prompt_fingerprint=ln.get("prompt_fingerprint", ""),
                inference_request_hash=ln.get("inference_request_hash", ""),
                provider=ln.get("provider", ""), model=ln.get("model", ""),
                retry_history=tuple(RetryRecord(attempt=r["attempt"], reason=r["reason"],
                                                delay_s=r.get("delay_s", 0.0), at=r.get("at", 0.0))
                                    for r in ln.get("retry_history", ())),
                tool_history=tuple(dict(x) for x in ln.get("tool_history", ())),
                streaming=_stream_from_dict(ln.get("streaming", {})),
                transformation=ln.get("transformation", "inference"),
                response_hash=ln.get("response_hash", "")),
            quality=dict(d.get("quality", {})),
            provider=d.get("provider", "echo"), model=d.get("model", "default"),
            request_hash=d.get("request_hash", ""), response_hash=d.get("response_hash", ""),
            response_version=d.get("response_version", "1"),
            created_at=d.get("created_at", 0.0), expires_at=d.get("expires_at"),
            org_id=d.get("org_id"), conversation_id=d.get("conversation_id"),
        )

    # ── ReasoningSession ─────────────────────────────────────────────────────
    def session_to_document(self, s: ReasoningSession) -> dict[str, Any]:
        return {"session_id": s.session_id, "scope": _scope_to_dict(s.scope),
                "response_ids": list(s.response_ids), "updated_at": s.updated_at,
                "metadata": dict(s.metadata)}

    def session_from_document(self, d: Mapping[str, Any]) -> ReasoningSession:
        return ReasoningSession(
            session_id=d["session_id"], scope=_scope_from_dict(d.get("scope", {})),
            response_ids=tuple(d.get("response_ids", ())),
            updated_at=d.get("updated_at", 0.0), metadata=dict(d.get("metadata", {})))

    # ── event payload + API view ──────────────────────────────────────────────
    def package_event_payload(self, pkg: ResponsePackage, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase, "id": pkg.id, "session_id": pkg.session_id, "state": pkg.state.value,
            "chunks": len(pkg.chunks), "text_length": len(pkg.text),
            "finish_reason": pkg.finish_reason.value, "provider": pkg.provider, "model": pkg.model,
            "response_hash": pkg.response_hash,
        }

    def usage_event_payload(self, pkg: ResponsePackage) -> dict[str, Any]:
        return {"id": pkg.id, "session_id": pkg.session_id, "provider": pkg.provider,
                "model": pkg.model, "prompt_tokens": pkg.usage.prompt_tokens,
                "completion_tokens": pkg.usage.completion_tokens,
                "total_tokens": pkg.usage.total_tokens}

    def package_to_api(self, pkg: ResponsePackage) -> dict[str, Any]:
        return {"id": pkg.id, "session_id": pkg.session_id, "state": pkg.state.value,
                "text": pkg.text, "finish_reason": pkg.finish_reason.value,
                "usage": pkg.usage.total_tokens, "provider": pkg.provider, "model": pkg.model}
