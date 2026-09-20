"""The I1 baseline command (M11.13): `python -m benchmarks.baseline`.

Produces a machine-readable JSON result and a human-readable summary for the runtime's own
`metric.turn` chain, under explicitly named profiles.

```
python -m benchmarks.baseline                      # both hermetic profiles, human summary
python -m benchmarks.baseline --json results.json  # + machine-readable artifact
python -m benchmarks.baseline --sessions 40        # a larger sample
```

**What a result is evidence of.** A `hermetic` profile measures the runtime's own cost on the
machine that ran it, with providers that return immediately. A `simulated` profile measures the
runtime's cost *around delays this harness injected*, and is the check that each delay lands in
the stage the runtime attributes it to. Neither is evidence about a vendor or a network: the
`live` profile is the only one that would be, and it requires credentials this repository does not
carry. The artifact records which profiles ran and which did not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass

from .harness import TurnSamples, measure, session_wall_clock
from .providers import DelayProfile
from .stats import Summary, render, summarize

#: Constitution §A8's per-stage p50 budgets, quoted for comparison **only**. A hermetic run cannot
#: pass or fail them — they describe a real conversation with real providers over a real network.
#: They appear in the artifact so a later live run has something to be compared against.
A8_BUDGETS_MS = {
    "endpoint": 250,
    "stt": 150,
    "llm": 350,
    "tts": 200,
    "voice_to_voice": 1000,
}

#: Profile B's injected delays. Chosen to be *distinguishable* — each stage gets a different value
#: so a misattributed delay is obvious in the output — and to sit in a plausible range rather than
#: to flatter any budget. They are the harness's numbers, not any vendor's.
SIMULATED_DELAYS = DelayProfile(
    stt_final_ms=40.0,
    llm_first_token_ms=120.0,
    tts_first_audio_ms=80.0,
    transport_send_ms=5.0,
)


@dataclass(frozen=True, slots=True)
class ProfileResult:
    name: str
    executed: bool
    reason: str = ""
    delays: dict | None = None
    cold: dict | None = None
    warm: dict | None = None


def environment() -> dict:
    """Everything needed to judge whether two runs are comparable. No secrets, by construction."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:  # pragma: no cover — git absent
        revision = ""
    return {
        "git_revision": revision,
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def live_provider_status() -> dict[str, str]:
    """Whether each vendor could be exercised. Presence of a credential **name** only — never a
    value, and the value is not read."""
    names = {
        "deepgram": "VOXVOICE_SECRET_DEEPGRAM_API_KEY",
        "groq": "VOXVOICE_SECRET_GROQ_API_KEY",
        "cartesia": "VOXVOICE_SECRET_CARTESIA_API_KEY",
    }
    return {
        provider: ("credential present" if os.environ.get(var) else "NOT EXECUTED — no credential")
        for provider, var in names.items()
    }


def summarize_samples(samples: TurnSamples) -> dict:
    """Turn collected samples into reportable summaries plus the honesty fields."""
    summaries: list[Summary] = [
        summarize(stage, values) for stage, values in sorted(samples.stages.items())
    ]
    if samples.voice_to_voice:
        summaries.append(summarize("voice_to_voice", samples.voice_to_voice))
    return {
        "turns": samples.turns,
        "failed_sessions": samples.failures,
        "missing_boundaries": samples.missing,
        "measurements": [s.as_dict() for s in summaries],
        "_summaries": summaries,
    }


async def run_profile(
    name: str, delays: DelayProfile, *, sessions: int, turns: int, warmup: int
) -> ProfileResult:
    cold, warm = await measure(
        delays, sessions=sessions, turns_per_session=turns, warmup_sessions=warmup
    )
    return ProfileResult(
        name=name,
        executed=True,
        delays=delays.as_dict(),
        cold=summarize_samples(cold),
        warm=summarize_samples(warm),
    )


async def telemetry_overhead(*, sessions: int, turns: int) -> dict:
    """Compare session wall-clock with the Epic 10 plane attached and detached.

    **Not** a production option: `runtime/` has no switch for this, and M11.12 did not add one.
    The plane is simply not supplied in one arm, which a composition root may already choose to
    do. The comparison measures whether wiring Epic 10 costs measurable time on the audio path —
    the question M11.12 left open — and nothing more."""
    arms: dict[str, list[float]] = {"wired": [], "unwired": []}
    for _ in range(sessions):
        arms["wired"].append(
            await session_wall_clock(DelayProfile(), turns=turns, telemetry=True, durable=True)
        )
        arms["unwired"].append(
            await session_wall_clock(DelayProfile(), turns=turns, telemetry=False, durable=False)
        )
    wired = summarize("session_wall_clock (Epic 10 wired)", arms["wired"])
    unwired = summarize("session_wall_clock (Epic 10 detached)", arms["unwired"])
    delta = None
    if wired.p50 is not None and unwired.p50 is not None:
        delta = wired.p50 - unwired.p50
    return {
        "measurements": [wired.as_dict(), unwired.as_dict()],
        "p50_difference_ms": delta,
        "note": (
            "Wall-clock includes the harness's paced frame delivery, which dominates both arms; "
            "only the difference is meaningful, and it is one machine's figure, not a general one."
        ),
        "_summaries": [wired, unwired],
    }


async def run_all(*, sessions: int, turns: int, warmup: int) -> dict:
    profiles = [
        await run_profile(
            "A-hermetic", DelayProfile(), sessions=sessions, turns=turns, warmup=warmup
        ),
        await run_profile(
            "B-simulated", SIMULATED_DELAYS, sessions=sessions, turns=turns, warmup=warmup
        ),
    ]
    overhead = await telemetry_overhead(sessions=max(5, sessions // 2), turns=turns)
    live = live_provider_status()
    profiles.append(
        ProfileResult(
            name="C-live",
            executed=False,
            reason="; ".join(f"{k}: {v}" for k, v in live.items()),
        )
    )
    return {
        "benchmark": "voxvoice.i1.baseline",
        "metric_source": "metric.turn (observability/turn_latency.py) — no second timing chain",
        "environment": environment(),
        "configuration": {
            "sessions_measured": sessions,
            "turns_per_session": turns,
            "warmup_sessions": warmup,
        },
        "a8_budgets_ms": A8_BUDGETS_MS,
        "telemetry_overhead": _strip(overhead),
        "live_providers": live,
        "stage2_host": "NOT EXECUTED — no Oracle A1 class host provisioned",
        "profiles": [
            {
                "name": p.name,
                "executed": p.executed,
                "reason": p.reason,
                "injected_delays_ms": p.delays,
                "cold": _strip(p.cold),
                "warm": _strip(p.warm),
            }
            for p in profiles
        ],
        "_profiles": profiles,
        "_overhead": overhead,
    }


def _strip(block: dict | None) -> dict | None:
    """Drop the private summary objects so the artifact is plain JSON."""
    if block is None:
        return None
    return {k: v for k, v in block.items() if not k.startswith("_")}


def render_report(result: dict) -> str:
    lines: list[str] = []
    env = result["environment"]
    lines.append("VoxVoice I1 latency baseline")
    lines.append(f"  source      : {result['metric_source']}")
    lines.append(
        f"  environment : {env['platform']} · {env['machine']} · "
        f"{env['cpu_count']} CPU · Python {env['python']}"
    )
    lines.append(f"  revision    : {env['git_revision'][:12] or 'unknown'}")
    cfg = result["configuration"]
    lines.append(
        f"  sampling    : {cfg['sessions_measured']} sessions × "
        f"{cfg['turns_per_session']} turns, {cfg['warmup_sessions']} warmup"
    )
    lines.append("")
    for profile in result["_profiles"]:
        if not profile.executed:
            lines.append(f"[{profile.name}] NOT EXECUTED — {profile.reason}")
            lines.append("")
            continue
        injected = profile.delays or {}
        note = (
            "no injected delay — runtime overhead only"
            if all(v == 0 for v in injected.values())
            else "injected delays: "
            + ", ".join(f"{k}={v:g}ms" for k, v in injected.items() if v)
        )
        lines.append(f"[{profile.name}] {note}")
        for label, block in (("cold (first session)", profile.cold), ("warm", profile.warm)):
            if not block or not block["_summaries"]:
                lines.append(f"  {label}: no turns measured")
                continue
            lines.append(f"  {label} — {block['turns']} turns, "
                         f"{block['failed_sessions']} failed sessions")
            lines.append(render(block["_summaries"]))
            if block["missing_boundaries"]:
                lines.append(f"  MISSING BOUNDARIES: {block['missing_boundaries']}")
            lines.append("")
    overhead = result.get("_overhead")
    if overhead:
        lines.append("[telemetry overhead] Epic 10 wired vs detached (harness wall-clock)")
        lines.append(render(overhead["_summaries"]))
        delta = overhead["p50_difference_ms"]
        if delta is not None:
            lines.append(f"  p50 difference: {delta:+.1f} ms per session — {overhead['note']}")
        lines.append("")
    lines.append(f"Stage-2 host: {result['stage2_host']}")
    lines.append(
        "These are hermetic measurements. They are not evidence about Deepgram, Groq, Cartesia, "
        "a network, or production behaviour."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoxVoice I1 latency baseline")
    parser.add_argument("--sessions", type=int, default=20, help="measured sessions per profile")
    parser.add_argument("--turns", type=int, default=2, help="turns per session")
    parser.add_argument("--warmup", type=int, default=2, help="warmup sessions (not measured)")
    parser.add_argument("--json", dest="json_path", default=None, help="write the JSON artifact")
    args = parser.parse_args(argv)

    result = asyncio.run(
        run_all(sessions=args.sessions, turns=args.turns, warmup=args.warmup)
    )
    print(render_report(result))
    if args.json_path:
        artifact = {k: v for k, v in result.items() if not k.startswith("_")}
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(artifact, handle, indent=2, sort_keys=True)
        print(f"\nJSON artifact written to {args.json_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
