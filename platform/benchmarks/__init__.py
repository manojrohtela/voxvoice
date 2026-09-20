"""benchmarks — the I1 latency baseline harness (M11.13).

**Not production architecture.** This package is excluded from the installed distribution (see
`pyproject.toml`'s package include-list, which names `core*`, `api*`, `runtime*`, `providers*`,
`events*`, `observability*` and `billing*` and nothing else). Nothing in `platform/` imports it;
it imports the runtime, never the reverse.

## What it measures, and what it refuses to

It drives the **real** conversation runtime and reads the **real** `metric.turn` the runtime
already produces (M11.12). It introduces no second timing chain, no second clock and no metric of
its own — `observability/turn_latency.py` remains the sole producer, and this package is a
consumer like any other.

What it cannot do is turn a hermetic measurement into a claim about production. A synthetic
profile measures *this machine running this harness*; a simulated profile measures *the runtime's
own overhead around delays the test itself injected*. Neither is evidence about Deepgram, Groq,
Cartesia or a network, and the report says so in those words.

## Layout

- `stats.py` — percentiles and summary statistics, with explicit honesty about small samples.
- `providers.py` — deterministic provider stand-ins with **configurable** delays (Profile B).
- `harness.py` — drives real turns through the production runtime and collects `metric.turn`.
- `baseline.py` — the CLI: `python -m benchmarks.baseline`.
"""

from __future__ import annotations

__all__: list[str] = []
