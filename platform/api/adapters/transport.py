"""Provider transport seam (M8.14).

The **only** place a network egress could happen — and it is injected, so tests run **offline
and deterministically**. A provider adapter builds a `ProviderRequest` (its provider-specific
endpoint/headers/body) and hands it to a `Transport`; the transport performs the wire call and
returns a `ProviderResponse` the adapter parses. Production injects an HTTP-backed transport
(e.g. httpx — documented, not a dependency here); tests inject `EchoTransport` (deterministic,
no sockets) or `FailingTransport` (to exercise retry/failure paths).

Keeping the socket behind this seam is what lets the **real** provider-translation code (request
shaping + response parsing) run in tests without any network, credentials, or provider SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    provider: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    status: int
    json: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    latency_ms: int = 0


class TransportError(Exception):
    """A transport-level failure (timeout/unavailable/etc.) — retryable by policy."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Transport(ABC):
    """The injectable wire seam. Deterministic implementations for tests; HTTP for prod."""

    @abstractmethod
    def request(self, req: ProviderRequest) -> ProviderResponse: ...

    @abstractmethod
    def stream(self, req: ProviderRequest) -> Iterator[dict[str, Any]]: ...


def _estimate(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


class EchoTransport(Transport):
    """Deterministic, offline transport. Echoes the request and returns a canned, provider-shaped
    response derived from the request body — so an adapter's request-shaping AND response-parsing
    both run without a network. No wall clock (latency derived from body size)."""

    def request(self, req: ProviderRequest) -> ProviderResponse:
        body_text = str(req.body)
        # A deterministic assistant reply + deterministic token counts derived from the request.
        reply = f"[{req.provider}] reply to {_estimate(body_text)} input tokens"
        return ProviderResponse(
            status=200,
            json={
                "echo": req.body,
                "provider": req.provider,
                "reply": reply,
                "usage": {"input": _estimate(body_text), "output": _estimate(reply)},
            },
            text=reply,
            latency_ms=_estimate(body_text),
        )

    def stream(self, req: ProviderRequest) -> Iterator[dict[str, Any]]:
        resp = self.request(req)
        for i, word in enumerate(resp.text.split()):
            yield {"type": "token", "seq": i, "text": word + " "}
        yield {"type": "done", "usage": resp.json["usage"], "latency_ms": resp.latency_ms}


class FailingTransport(Transport):
    """Always raises — for exercising retry / circuit-breaker / failure paths deterministically."""

    def __init__(self, *, retryable: bool = True, message: str = "provider unavailable") -> None:
        self._retryable = retryable
        self._message = message

    def request(self, req: ProviderRequest) -> ProviderResponse:
        raise TransportError(self._message, retryable=self._retryable)

    def stream(self, req: ProviderRequest) -> Iterator[dict[str, Any]]:
        raise TransportError(self._message, retryable=self._retryable)


class HttpTransport(Transport):
    """The **production** wire transport (M8.18 hardening). A real HTTP egress over the standard
    library (`urllib`) — **no new dependency, no provider SDK** — with per-request timeout, a
    bounded connection pool (an `http.client` connection cached per host — the stdlib's keep-alive),
    and typed `TransportError`s (5xx / network → retryable; 4xx → non-retryable). Never the
    default; a deployment injects it at the composition root, and tests keep `EchoTransport` so the
    suite stays offline. Streaming falls back to a single buffered chunk (server-sent events are a
    later enhancement behind the same seam)."""

    def __init__(self, *, timeout_seconds: float = 30.0, max_pool_per_host: int = 8) -> None:
        self._timeout = timeout_seconds
        self._max_pool = max_pool_per_host
        self._pool: dict[str, list[Any]] = {}

    def _connection(self, url: str):
        # Reuse a pooled connection per host (bounded) — the stdlib keep-alive path.
        from http.client import HTTPConnection, HTTPSConnection
        from urllib.parse import urlparse

        parts = urlparse(url)
        host = parts.netloc
        pool = self._pool.setdefault(host, [])
        if pool:
            return pool.pop(), parts
        cls = HTTPSConnection if parts.scheme == "https" else HTTPConnection
        return cls(host, timeout=self._timeout), parts

    def _release(self, host: str, conn) -> None:
        pool = self._pool.setdefault(host, [])
        if len(pool) < self._max_pool:
            pool.append(conn)
        else:
            conn.close()

    def request(self, req: ProviderRequest) -> ProviderResponse:
        import json as _json
        import time as _time
        from urllib.parse import urlparse

        parts = urlparse(req.url)
        conn, _ = self._connection(req.url)
        started = _time.perf_counter()
        try:
            body = _json.dumps(req.body).encode() if req.body else None
            headers = {"Content-Type": "application/json", **req.headers}
            conn.request(req.method, parts.path or "/", body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
        except (OSError, TimeoutError) as exc:  # network / timeout → retryable
            conn.close()
            raise TransportError(f"transport error: {exc}", retryable=True) from exc
        finally:
            self._release(parts.netloc, conn)
        latency_ms = int((_time.perf_counter() - started) * 1000)
        if status >= 500:
            raise TransportError(f"provider {status}", retryable=True)
        if status >= 400:
            raise TransportError(f"provider {status}: {raw[:200]}", retryable=False)
        try:
            parsed = _json.loads(raw) if raw else {}
        except ValueError:
            parsed = {}
        return ProviderResponse(status=status, json=parsed, text=raw, latency_ms=latency_ms)

    def stream(self, req: ProviderRequest) -> Iterator[dict[str, Any]]:
        resp = self.request(req)
        yield {"type": "chunk", "status": resp.status, "text": resp.text}
        yield {"type": "done", "latency_ms": resp.latency_ms}

    def close(self) -> None:
        for pool in self._pool.values():
            for conn in pool:
                conn.close()
        self._pool.clear()
