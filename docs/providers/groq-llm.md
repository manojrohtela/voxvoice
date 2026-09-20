# Groq Streaming LLM

**Status:** Active v1.0 (2026-09-17) · **Owner:** Technical Owner · **Milestone:** M11.9
**Authority:** [ADR-0048](../adr/ADR-0048-llm-tts-clause-pipelining-and-playback.md) PB1/PB2, [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [ADR-0049](../adr/ADR-0049-provider-credential-injection.md), [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md) §3, AR-11, AR-12, AR-20, AR-21, AR-23, AR-25
**Decisions:** DL-0188 (dependency and licensing), DL-0189 (design), DL-0191 (limitations and risks)

> **LLM streaming produces incremental text; clause detection decides when text is ready for the
> TTS pipeline.** This provider produces the text. It creates no clause, calls no TTS, plays no
> audio and makes no turn decision — see [clause pipelining](../runtime/clause-pipelining.md).

## Architecture

```
ConversationOrchestrator / RuntimePipeline._generate
        │  assembled, provider-neutral context
        ▼
ProviderGuard("llm")  ── deadline · typed errors · breaker (AR-23, unchanged)
        ▼
GroqLLM.stream()  ─────► TokenDelta(text=…) as the model produces it
        │  CompletionSpec / CompletionChunk — text only
        ▼
GroqCompletionClient ──► groq.AsyncGroq  ◄── the only SDK import in the repository
```

| Layer | Owns | Knows the SDK? |
|---|---|---|
| `providers/llm/groq_client.py` | the `groq` import (lazy), client construction, chunk reading, error classification | **yes — and it is the only one** (AST-asserted) |
| `providers/llm/groq.py` | context validation, chunk normalization, typed failures, stream lifecycle, capabilities | no |
| `providers/llm/completion_stream.py` | the neutral Protocols between them | no |

`GroqLLM` implements the **unchanged** `LLMProvider` contract. **No core or contract change was
needed**: `stream()` has been an `AsyncIterator[TokenDelta | ToolCallDelta]` since M2C.2, and the
additive `model` argument arrived at M11.4. M11.9 adds no field to the contract.

## Library

- **Package:** `groq` 1.7.0 (Groq, Inc.), **Apache-2.0**.
- **New transitive dependencies:** `distro` (Apache-2.0) and `sniffio` (MIT OR Apache-2.0) only.
  `anyio`, `httpx`, `pydantic` and `typing-extensions` were already in the tree. 49 → **52**
  locked packages. `pip-audit`: no known vulnerabilities.
- **No framework was added.** No LangChain, LangGraph, Pipecat or agent substrate — the repository
  remains first-party (AR-13, P-ADR-0002).

## The normalized chunk contract

A chunk crosses the seam carrying **text and nothing else**. Deliberately absent, because the
runtime consumes none of them and a field nothing reads is the accepted-and-ignored configuration
AR-25 forbids: tool calls, token counts, log-probabilities, finish reasons, usage, and timing.
Latency instants belong to ADR-0038's taps against one injected clock; a provider-supplied
timestamp would be a second, unreconcilable clock.

## Streaming semantics

- **Genuinely incremental.** `stream()` yields each piece of text as the vendor produces it. The
  response is never accumulated and then replayed — a mutant that buffers the whole completion
  before yielding is killed by a gated test, because the assertion is made *while* the vendor is
  provably still generating.
- **Empty chunks are dropped, not yielded.** Role-only and finish-only chunks carry no text, and a
  `TokenDelta(text="")` would make `llm_first_token` untrue.
- **No text is altered.** Not stripped, not joined, not re-punctuated. Segmentation is the
  runtime's decision, not a vendor plugin's.
- **The SDK's own retries are switched off** (`max_retries=0`). `ProviderGuard` owns retry, breaker
  and failover policy; a second invisible retry layer would triple a failing call's latency budget
  and corrupt the breaker's failure accounting.

## Failures and degradation

| Cause | Surfaces as | Runtime handling |
|---|---|---|
| request deadline exceeded | `ProviderTimeout` | existing M11.4 guard path |
| vendor unreachable, 5xx | `ProviderUnavailable` | existing guard path |
| credential rejected (401/403) | `ProviderAuthError` | existing guard path |
| rate limited or out of quota (429) | `ProviderRateLimited` | existing guard path |
| unknown model, malformed request (400/404/422), unparseable answer | `ProviderProtocolError` | existing guard path |
| generation stopped with no finish reason | `ProviderStreamTruncated` | existing guard path |
| tools requested | `CapabilityUnsupported` | refused, never silently served |

Classification is written against the SDK's **exception classes and HTTP statuses**, never message
text: vendor messages are not a contract, whereas a 401 is a 401. Vendor text is never passed
through — it routinely embeds endpoints, account identifiers and occasionally credential fragments
(ADR-0049 CR5/CR6). `asyncio.CancelledError` is never translated: the caller withdrawing is not the
vendor failing, and counting it as one would trip the breaker on every interrupted turn.

**No LLM-specific breaker, retry or failover exists.** The runtime governs the LLM through
`ProviderGuard` exactly as M11.4 established.

### A documented limitation of failover for streaming LLMs

`call_with_failover` selects a candidate **before** the call begins. Once tokens have been
delivered, switching providers cannot be transparent — the caller has already heard part of an
answer, and a second model would continue a sentence it did not start. M11.9 therefore does not
apply failover mid-stream, and does not invent a mechanism for it (R-56).

## Cancellation

`aclose()` runs on **every** exit path — normal end, failure, and the caller closing the generator
when a turn is cancelled — so an abandoned turn cannot leave a vendor connection open. Proven at
four points: during setup, before the first chunk, mid-stream, and after the final chunk. A
mid-stream cancellation is asserted to stop the provider **pulling** from the vendor, not merely to
stop it yielding. The provider starts no task of its own, so none can be left behind.

## Configuration

```python
bootstrap.build_registry(llm="groq", credentials=…)
bootstrap.build_session_controller(llm="groq", credentials=…, llm_model="llama-3.1-8b-instant")
```

- **Default unchanged: `echo`.** `EchoLLM` remains the default for every existing caller.
- `"groq"` resolves exactly one credential — `groq_api_key`
  (`VOXVOICE_SECRET_GROQ_API_KEY`) — through the injected ADR-0049 `CredentialProvider`, at the
  composition root and nowhere else. The plugin never reads the environment (CR4, AST-asserted).
- Missing credential, or a host without the SDK → **`ValueError` at startup**, naming what is
  missing. **There is no fallback to `EchoLLM`**: an operator who asked for a real model and
  silently got a canned echo would be told a lie about their own configuration.
- `llm_client=` is the hermetic test seam (a deterministic client; no SDK, no credential).

## Declared capabilities

| Flag | Value | Why |
|---|---|---|
| `tools` | `False` | tool calling is not implemented; requesting tools is **refused** |
| `structured_output` | `False` | not implemented |
| `models` | `()` | the contract defines empty as *"does not enumerate"*; Groq's catalogue changes without a release of ours, and a hardcoded whitelist would both reject valid models and assert something we never verified |
| `context_window` | `131072` | the vendor's published figure for `llama-3.3-70b-versatile`, **declared, not measured**; a constructor argument so an operator on another model states the right number |

Nothing else is claimed. No multilingual, reasoning, quality or latency capability is declared,
because none was verified.

## Testing

| File | What it proves |
|---|---|
| `tests/test_groq_llm.py` | contract, capabilities, incremental streaming under a gate, the full failure taxonomy, context validation, cancellation at four points, no task leak |
| `tests/test_groq_client.py` | classification, chunk reading, truncation detection, credential redaction, idempotent close — against a stand-in `groq` module |
| `tests/test_llm_streaming_integration.py` | selection, refusal, default preservation, clause delivery through the pipeline, and the AST import boundaries |
| `tests/fake_completion.py` | the deterministic vendor, its gates, and a recording sink |

**No test needs the SDK, a credential or a network.** The hermetic suite runs in a bare dev venv
with no vendor package installed.

## Known limitations

- **No live call was ever made.** Every behaviour here is proven against a deterministic fake and
  against the installed SDK's own classes. How Groq's servers actually behave — chunk cadence,
  error bodies, rate-limit headers — is unverified (R-54).
- No accuracy, quality, latency or throughput claim of any kind. No measurement was taken (R-47).
- The context window is a declared vendor figure, not a counted one (R-55).
- No tool calling, no structured output, no failover mid-stream (R-56).
- Conversation context remains the M2C.2 minimum (system prompt + current utterance). Memory and
  the Prompt Runtime are M5's; M11.9 deliberately redesigned neither.
