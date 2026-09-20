"""Deterministic probes for properties that would otherwise need a wall clock (M11.3).

ADR-0045 **SD7** specifies `VoiceActivityProvider.detect()` as **O(1) and non-blocking**. The
obvious way to test that is a stopwatch and a threshold, and it is the wrong way: TESTING_POLICY
rule 2 forbids timing-dependent synchronisation, and a threshold tuned on one machine is a flake
waiting for a slower one.

So the properties are measured by **what a call does**, not how long it took:

* `BlockingIOSentinel` fails the test if a call touches the filesystem, the network, or `sleep` —
  the three things "non-blocking" actually excludes. It patches the syscalls rather than trusting
  a reviewer to notice them.
* `CostProbe` counts per-call work against frame size. O(1) means the count does not grow with the
  payload, which is a *ratio* and therefore machine-independent.

A coarse wall-clock bound is available as a last-resort sanity check, deliberately set orders of
magnitude above any plausible O(1) call so it cannot flake — it exists to catch a provider that
blocks for a second, not to measure microseconds.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


class BlockingCallDetected(AssertionError):
    """A call that must be non-blocking performed blocking I/O."""


@contextmanager
def BlockingIOSentinel(what: str) -> Iterator[None]:
    """Fail if the enclosed code sleeps, opens a socket, or opens a file.

    Not a sandbox and not trying to be one — it is a targeted guard on the three operations that
    make a hot-path call block, installed for the duration of one call so a provider cannot pass
    conformance by doing its I/O somewhere the reviewer did not look."""
    import builtins

    real_sleep, real_socket, real_open = time.sleep, socket.socket, builtins.open

    def _no_sleep(*a: Any, **k: Any) -> None:
        raise BlockingCallDetected(f"{what} called time.sleep()")

    def _no_socket(*a: Any, **k: Any) -> None:
        raise BlockingCallDetected(f"{what} opened a socket")

    def _no_open(*a: Any, **k: Any) -> None:
        raise BlockingCallDetected(f"{what} opened a file")

    time.sleep, socket.socket, builtins.open = _no_sleep, _no_socket, _no_open
    try:
        yield
    finally:
        time.sleep, socket.socket, builtins.open = real_sleep, real_socket, real_open


@dataclass
class CostProbe:
    """Counts the work one call performs, so O(1) is a ratio rather than a stopwatch reading."""

    samples: dict[int, int] = field(default_factory=dict)

    def measure(self, size: int, call: Callable[[], Any]) -> Any:
        """Run `call`, attributing its bytecode-level work to an input of `size` bytes."""
        import sys

        counter = 0

        def _trace(frame: Any, event: str, arg: Any) -> Any:
            nonlocal counter
            if event == "call":
                counter += 1
            return None

        previous = sys.getprofile()
        sys.setprofile(_trace)
        try:
            result = call()
        finally:
            sys.setprofile(previous)
        self.samples[size] = counter
        return result

    def is_constant(self, *, tolerance: int = 2) -> bool:
        """Whether measured work stayed flat as the input grew.

        `tolerance` is an absolute allowance for incidental variation between two runs, not a
        growth factor: an O(n) implementation over inputs differing by orders of magnitude will
        exceed it by a wide margin, while a genuinely constant one varies by zero or one call."""
        if len(self.samples) < 2:
            return True
        counts = list(self.samples.values())
        return max(counts) - min(counts) <= tolerance

    def report(self) -> str:
        return ", ".join(f"{size}B→{count} calls" for size, count in sorted(self.samples.items()))


#: A wall-clock ceiling for one `detect()` call. Set at 50 ms — roughly four orders of magnitude
#: above any plausible constant-time call, so it flags a provider that genuinely blocks and can
#: not realistically flake on a loaded machine. The structural probes above are the real
#: assertions; this is a backstop.
DETECT_WALL_CLOCK_CEILING_S = 0.050


__all__ = [
    "DETECT_WALL_CLOCK_CEILING_S",
    "BlockingCallDetected",
    "BlockingIOSentinel",
    "CostProbe",
]
