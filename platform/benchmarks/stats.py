"""Summary statistics for the I1 baseline, with the honesty the numbers require (M11.13).

Two rules govern everything here, and both exist because a latency table is the easiest place in
an engineering document to imply precision nobody measured:

1. **A percentile a sample cannot support is not reported.** `p99` of 20 samples is the maximum
   wearing a different name. Rather than print it and hope a reader notices the sample size, the
   value is `None` and `supported` says why.
2. **Zero variance is stated, not hidden.** A deterministic harness produces identical samples;
   printing `p50 = p95 = p99` without comment invites the reader to conclude the *system* is
   consistent, when what is consistent is the fake.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: A percentile is reported only when the sample is large enough for the *rank* it names to be a
#: real observation rather than a restatement of the maximum. `p95` needs 20 samples for the 95th
#: percentile to fall on a distinct rank; `p99` needs 100. Below that the value is withheld.
MINIMUM_SAMPLES = {"p50": 2, "p95": 20, "p99": 100}


def percentile(ordered: list[float], fraction: float) -> float:
    """The nearest-rank percentile of an already-sorted sample.

    Nearest-rank rather than interpolated: an interpolated percentile invents a value that was
    never observed, which is the wrong default for latency evidence."""
    if not ordered:
        raise ValueError("percentile of an empty sample")
    # The standard nearest-rank definition: ceil(p x N), clamped into the sample. An earlier
    # `round(p*N + 0.5)` was off by one at the boundaries — p95 of 100 samples returned the 96th.
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


@dataclass(frozen=True, slots=True)
class Summary:
    """One measured quantity. Every field a reader needs to judge the number is present."""

    name: str
    unit: str
    count: int
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    #: Percentiles withheld because the sample is too small, with the threshold that was applied.
    withheld: dict[str, int] = field(default_factory=dict)
    #: True when every sample is identical — a deterministic harness, not a consistent system.
    degenerate: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "unit": self.unit,
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
            "p50": self.p50,
            "p95": self.p95,
            "p99": self.p99,
            "withheld_percentiles": self.withheld,
            "zero_variance": self.degenerate,
        }


def summarize(name: str, samples: list[float], *, unit: str = "ms") -> Summary:
    """Summarize one quantity, withholding percentiles the sample cannot support."""
    if not samples:
        return Summary(name=name, unit=unit, count=0)
    ordered = sorted(samples)
    count = len(ordered)
    values: dict[str, float | None] = {}
    withheld: dict[str, int] = {}
    for label, fraction in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        needed = MINIMUM_SAMPLES[label]
        if count >= needed:
            values[label] = percentile(ordered, fraction)
        else:
            values[label] = None
            withheld[label] = needed
    return Summary(
        name=name,
        unit=unit,
        count=count,
        minimum=ordered[0],
        maximum=ordered[-1],
        mean=sum(ordered) / count,
        p50=values["p50"],
        p95=values["p95"],
        p99=values["p99"],
        withheld=withheld,
        degenerate=ordered[0] == ordered[-1],
    )


def render(summaries: list[Summary]) -> str:
    """A human-readable table. Withheld percentiles render as `—`, never as a number."""

    def cell(value: float | None) -> str:
        return "—" if value is None else f"{value:.1f}"

    header = f"{'measurement':<28}{'n':>6}{'min':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}"
    lines = [header, "-" * len(header)]
    for s in summaries:
        lines.append(
            f"{s.name:<28}{s.count:>6}{cell(s.minimum):>9}{cell(s.p50):>9}"
            f"{cell(s.p95):>9}{cell(s.p99):>9}{cell(s.maximum):>9}"
        )
    notes = sorted({label for s in summaries for label in s.withheld})
    if notes:
        thresholds = ", ".join(f"{label} needs {MINIMUM_SAMPLES[label]}" for label in notes)
        lines.append(f"\n— = withheld: the sample is too small for that rank ({thresholds}).")
    if any(s.degenerate and s.count > 1 for s in summaries):
        lines.append(
            "Some measurements have zero variance: that is the deterministic harness, "
            "not evidence that the system is consistent."
        )
    return "\n".join(lines)


__all__ = ["MINIMUM_SAMPLES", "Summary", "percentile", "render", "summarize"]
