"""Provider serialization (M5.6B-1).

Deterministic, vendor-neutral translation between the domain aggregates (`ProviderRequest`,
`ProviderChunk`, `ProviderResponse`, `ProviderSession`) and the plain dict shapes used by
storage, events, and the future API. Round-trippable; no I/O. Stateless — safe to share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    ChunkKind,
    FinishReason,
    GenerationParameters,
    ProviderChunk,
    ProviderMessage,
    ProviderMetadata,
    ProviderRequest,
    ProviderRequestMetadata,
    ProviderRequestState,
    ProviderResponse,
    ProviderScope,
    ProviderSession,
    ProviderUsage,
    StreamingMetadata,
)


def _scope_to_dict(s: ProviderScope) -> dict[str, Any]:
    return {"org_id": s.org_id, "conversation_id": s.conversation_id,
            "session_id": s.session_id, "region": s.region}


def _scope_from_dict(d: Mapping[str, Any]) -> ProviderScope:
    return ProviderScope(org_id=d.get("org_id"), conversation_id=d.get("conversation_id"),
                         session_id=d.get("session_id"), region=d.get("region"))


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


class ProviderSerializer:
    """Stateless, deterministic (de)serializer for provider aggregates."""

    # ── ProviderChunk ─────────────────────────────────────────────────────────
    def chunk_to_dict(self, c: ProviderChunk) -> dict[str, Any]:
        return {"id": c.id, "index": c.index, "delta": c.delta, "kind": c.kind.value,
                "done": c.done, "metadata": dict(c.metadata)}

    def chunk_from_dict(self, d: Mapping[str, Any]) -> ProviderChunk:
        return ProviderChunk(id=d["id"], index=d["index"], delta=d["delta"],
                             kind=ChunkKind(d.get("kind", ChunkKind.TOKEN.value)),
                             done=d.get("done", False), metadata=dict(d.get("metadata", {})))

    # ── ProviderRequest ───────────────────────────────────────────────────────
    def request_to_dict(self, r: ProviderRequest) -> dict[str, Any]:
        m = r.metadata
        return {
            "session_id": r.session_id,
            "messages": [{"role": x.role, "content": x.content, "metadata": dict(x.metadata)}
                         for x in r.messages],
            "capability": r.capability, "provider": r.provider, "model": r.model,
            "generation": _gen_to_dict(r.generation),
            "streaming": r.streaming, "timeout_s": r.timeout_s,
            "metadata": {"request": dict(m.request), "conversation": dict(m.conversation),
                         "runtime": dict(m.runtime)},
            "request_hash": r.request_hash, "version": r.version,
            "scope": _scope_to_dict(r.scope),
        }

    def request_from_dict(self, d: Mapping[str, Any]) -> ProviderRequest:
        m = d.get("metadata", {})
        return ProviderRequest(
            session_id=d["session_id"],
            messages=tuple(
                ProviderMessage(role=x["role"], content=x["content"],
                                metadata=dict(x.get("metadata", {})))
                for x in d.get("messages", ())
            ),
            capability=d.get("capability", "chat"),
            provider=d.get("provider", ""), model=d.get("model", "default"),
            generation=_gen_from_dict(d.get("generation", {})),
            streaming=d.get("streaming", True), timeout_s=d.get("timeout_s"),
            metadata=ProviderRequestMetadata(request=dict(m.get("request", {})),
                                            conversation=dict(m.get("conversation", {})),
                                            runtime=dict(m.get("runtime", {}))),
            request_hash=d.get("request_hash", ""), version=d.get("version", "1"),
            scope=_scope_from_dict(d.get("scope", {})),
        )

    # ── ProviderResponse ──────────────────────────────────────────────────────
    def response_to_document(self, resp: ProviderResponse) -> dict[str, Any]:
        md = resp.metadata
        return {
            "id": resp.id, "session_id": resp.session_id, "scope": _scope_to_dict(resp.scope),
            "state": resp.state.value, "text": resp.text,
            "chunks": [self.chunk_to_dict(c) for c in resp.chunks],
            "usage": {"prompt_tokens": resp.usage.prompt_tokens,
                      "completion_tokens": resp.usage.completion_tokens,
                      "cached_tokens": resp.usage.cached_tokens,
                      "reasoning_tokens": resp.usage.reasoning_tokens,
                      "image_units": resp.usage.image_units,
                      "audio_seconds": resp.usage.audio_seconds,
                      "embedding_tokens": resp.usage.embedding_tokens},
            "finish_reason": resp.finish_reason.value,
            "streaming": _stream_to_dict(resp.streaming),
            "timing": dict(resp.timing),
            "metadata": {"provider": md.provider, "model": md.model,
                         "model_version": md.model_version, "latency_ms": md.latency_ms,
                         "duration_ms": md.duration_ms, "request_id": md.request_id,
                         "response_id": md.response_id, "region": md.region,
                         "organization": md.organization, "retries": md.retries,
                         "endpoint": md.endpoint, "pricing_metadata": dict(md.pricing_metadata),
                         "extra": dict(md.extra)},
            "provider": resp.provider, "model": resp.model,
            "request_hash": resp.request_hash, "response_hash": resp.response_hash,
            "response_version": resp.response_version,
            "created_at": resp.created_at, "expires_at": resp.expires_at,
            "org_id": resp.org_id, "conversation_id": resp.conversation_id,
        }

    def response_from_document(self, d: Mapping[str, Any]) -> ProviderResponse:
        u = d.get("usage", {})
        md = d.get("metadata", {})
        return ProviderResponse(
            id=d["id"], session_id=d.get("session_id", ""),
            scope=_scope_from_dict(d.get("scope", {})),
            state=ProviderRequestState(d.get("state", ProviderRequestState.REQUESTED.value)),
            text=d.get("text", ""),
            chunks=tuple(self.chunk_from_dict(x) for x in d.get("chunks", ())),
            usage=ProviderUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                cached_tokens=u.get("cached_tokens", 0),
                reasoning_tokens=u.get("reasoning_tokens", 0),
                image_units=u.get("image_units", 0), audio_seconds=u.get("audio_seconds", 0.0),
                embedding_tokens=u.get("embedding_tokens", 0)),
            finish_reason=FinishReason(d.get("finish_reason", FinishReason.STOP.value)),
            streaming=_stream_from_dict(d.get("streaming", {})),
            timing=dict(d.get("timing", {})),
            metadata=ProviderMetadata(
                provider=md.get("provider", ""), model=md.get("model", ""),
                model_version=md.get("model_version", ""), latency_ms=md.get("latency_ms", 0.0),
                duration_ms=md.get("duration_ms", 0.0), request_id=md.get("request_id", ""),
                response_id=md.get("response_id", ""), region=md.get("region"),
                organization=md.get("organization"), retries=md.get("retries", 0),
                endpoint=md.get("endpoint"),
                pricing_metadata=dict(md.get("pricing_metadata", {})),
                extra=dict(md.get("extra", {}))),
            provider=d.get("provider", "echo"), model=d.get("model", "default"),
            request_hash=d.get("request_hash", ""), response_hash=d.get("response_hash", ""),
            response_version=d.get("response_version", "1"),
            created_at=d.get("created_at", 0.0), expires_at=d.get("expires_at"),
            org_id=d.get("org_id"), conversation_id=d.get("conversation_id"),
        )

    # ── ProviderSession ───────────────────────────────────────────────────────
    def session_to_document(self, s: ProviderSession) -> dict[str, Any]:
        return {"session_id": s.session_id, "scope": _scope_to_dict(s.scope),
                "response_ids": list(s.response_ids), "updated_at": s.updated_at,
                "metadata": dict(s.metadata)}

    def session_from_document(self, d: Mapping[str, Any]) -> ProviderSession:
        return ProviderSession(
            session_id=d["session_id"], scope=_scope_from_dict(d.get("scope", {})),
            response_ids=tuple(d.get("response_ids", ())),
            updated_at=d.get("updated_at", 0.0), metadata=dict(d.get("metadata", {})))

    # ── event payload + API view ──────────────────────────────────────────────
    def response_event_payload(self, resp: ProviderResponse, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase, "id": resp.id, "session_id": resp.session_id,
            "state": resp.state.value, "chunks": len(resp.chunks), "text_length": len(resp.text),
            "finish_reason": resp.finish_reason.value, "provider": resp.provider,
            "model": resp.model, "response_hash": resp.response_hash,
        }

    def usage_event_payload(self, resp: ProviderResponse) -> dict[str, Any]:
        return {"id": resp.id, "session_id": resp.session_id, "provider": resp.provider,
                "model": resp.model, "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens}

    def response_to_api(self, resp: ProviderResponse) -> dict[str, Any]:
        return {"id": resp.id, "session_id": resp.session_id, "state": resp.state.value,
                "text": resp.text, "finish_reason": resp.finish_reason.value,
                "usage": resp.usage.total_tokens, "provider": resp.provider, "model": resp.model}
