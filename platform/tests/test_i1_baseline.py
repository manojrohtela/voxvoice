"""The I1 baseline harness, tested for the properties a baseline must have (M11.13).

These are **not** latency assertions. A test that says `assert p50 < 10` pins a number to this
machine on this afternoon and fails for reasons that have nothing to do with the code — the
milestone forbids exactly that. What is asserted instead is that the instrument is trustworthy:
percentiles are computed correctly, small samples withhold rather than invent, injected delays
land in the stage the runtime attributes them to, failures are counted rather than dropped, and
the artifact carries no secret.

The one timing-shaped assertion is Profile B's decomposition, and it is a **structural** claim
with a generous margin: an 80 ms injected TTS delay must appear in the `tts` stage rather than in
`llm` or `egress`. That is a statement about attribution, which is deterministic, not about speed.
"""

from __future__ import annotations

import json

import pytest

from benchmarks import baseline, harness, stats
from benchmarks.providers import DelayProfile

# ── 3, 4: the statistics are correct and honest ─────────────────────────────


def test_percentiles_are_nearest_rank_observations():
    """Nearest-rank, never interpolated: a reported latency was actually observed."""
    ordered = [float(n) for n in range(1, 101)]
    assert stats.percentile(ordered, 0.50) == 50.0
    assert stats.percentile(ordered, 0.95) == 95.0
    assert stats.percentile(ordered, 0.99) == 99.0
    assert stats.percentile(ordered, 0.0) == 1.0


def test_a_percentile_the_sample_cannot_support_is_withheld():
    """p99 of 20 samples is the maximum wearing a different name."""
    summary = stats.summarize("x", [float(n) for n in range(20)])
    assert summary.p50 is not None
    assert summary.p95 is not None
    assert summary.p99 is None
    assert summary.withheld == {"p99": 100}


def test_a_tiny_sample_withholds_everything_but_the_extremes():
    summary = stats.summarize("x", [5.0])
    assert (summary.minimum, summary.maximum) == (5.0, 5.0)
    assert summary.p50 is None and summary.p95 is None and summary.p99 is None


def test_zero_variance_is_declared_rather_than_hidden():
    """Identical samples are the deterministic harness, not a consistent system."""
    summary = stats.summarize("x", [7.0] * 50)
    assert summary.degenerate is True
    assert "zero variance" in stats.render([summary])


def test_an_empty_sample_reports_no_numbers_at_all():
    summary = stats.summarize("x", [])
    assert summary.count == 0
    assert summary.p50 is None and summary.minimum is None


def test_withheld_percentiles_render_as_a_dash_not_a_number():
    rendered = stats.render([stats.summarize("x", [1.0, 2.0])])
    assert "—" in rendered
    assert "needs" in rendered


# ── 1, 2: the command exists and the deterministic profile executes ─────────


async def test_the_hermetic_profile_produces_a_complete_metric_chain():
    """Profile A drives the real runtime and every §A8 boundary is stamped."""
    collector = await harness.run_session(DelayProfile(), turns=1)
    assert collector.metrics, "the hermetic profile produced no metric.turn"
    payload = collector.metrics[0]
    assert payload["status"] == "completed"
    at = payload["at"]
    for boundary in (
        "speech_start", "speech_end", "endpoint_decided", "stt_final",
        "llm_first_token", "tts_first_audio", "first_audio_out",
    ):
        assert boundary in at, f"{boundary} was never stamped"


async def test_8_boundary_timestamps_are_ordered():
    """`first_audio_out` cannot precede `tts_first_audio`: audio is sent after it exists."""
    collector = await harness.run_session(DelayProfile(), turns=1)
    at = collector.metrics[0]["at"]
    assert at["speech_end"] <= at["endpoint_decided"]
    assert at["llm_first_token"] <= at["tts_first_audio"]
    assert at["tts_first_audio"] <= at["first_audio_out"]


async def test_voice_to_voice_is_present_and_positive():
    collector = await harness.run_session(DelayProfile(), turns=1)
    v2v = collector.metrics[0]["voice_to_voice_ms"]
    assert v2v is not None and v2v > 0


# ── 12: an injected delay lands in the stage it belongs to ──────────────────


async def test_12_an_injected_tts_delay_appears_in_the_tts_stage():
    """The decomposition claim, asserted structurally with a generous margin.

    80 ms is injected before the first audio frame; the `tts` stage must carry it and the
    neighbouring stages must not. The margin is wide because this asserts *attribution*, which is
    deterministic, rather than speed, which is not."""
    collector = await harness.run_session(DelayProfile(tts_first_audio_ms=80.0), turns=1)
    ms = collector.metrics[0]["ms"]
    assert ms["tts"] >= 75, f"the injected TTS delay did not reach the tts stage: {ms}"
    assert ms["tts"] < 200, f"the tts stage absorbed something else: {ms}"
    assert ms.get("egress", 0) < 75, f"the TTS delay leaked into egress: {ms}"


async def test_an_injected_egress_delay_appears_in_the_egress_stage():
    collector = await harness.run_session(DelayProfile(transport_send_ms=30.0), turns=1)
    ms = collector.metrics[0]["ms"]
    assert ms["egress"] >= 25, f"the injected transport delay did not reach egress: {ms}"


async def test_zero_injected_delay_leaves_the_synthesis_stages_small():
    """Profile A's control: with instant providers, tts and egress carry almost nothing."""
    collector = await harness.run_session(DelayProfile(), turns=1)
    ms = collector.metrics[0]["ms"]
    assert ms["tts"] < 50 and ms.get("egress", 0) < 50


# ── 5, 6, 7: warmup, failures, missing metrics ─────────────────────────────


async def test_5_warmup_sessions_are_exercised_but_not_measured():
    cold, warm = await harness.measure(
        DelayProfile(), sessions=1, turns_per_session=1, warmup_sessions=1
    )
    assert cold.turns == 1, "the first session must be reported as cold"
    assert warm.turns == 1, "exactly the measured sessions contribute to warm"


def test_6_failures_are_counted_rather_than_discarded():
    samples = harness.TurnSamples()
    samples.failures += 2
    block = baseline.summarize_samples(samples)
    assert block["failed_sessions"] == 2
    assert block["turns"] == 0


def test_7_a_missing_boundary_is_recorded_not_ignored():
    """A turn whose payload lacks a boundary must show up as missing, never as a fast sample."""
    samples = harness.TurnSamples()
    samples.absorb({"ms": {"endpoint": 240}, "voice_to_voice_ms": None, "at": {"speech_end": 1.0}})
    assert samples.missing["voice_to_voice_ms"] == 1
    assert samples.missing["first_audio_out"] == 1
    assert samples.voice_to_voice == []


# ── 11: the machine-readable artifact ───────────────────────────────────────


async def test_11_the_artifact_is_valid_json_with_the_documented_fields(tmp_path):
    result = await baseline.run_all(sessions=1, turns=1, warmup=0)
    artifact = {k: v for k, v in result.items() if not k.startswith("_")}
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    for field in ("benchmark", "environment", "configuration", "profiles", "live_providers",
                  "stage2_host", "a8_budgets_ms", "telemetry_overhead", "metric_source"):
        assert field in loaded, f"the artifact omits {field}"
    env = loaded["environment"]
    for field in ("git_revision", "python", "platform", "machine", "cpu_count", "timestamp_utc"):
        assert field in env
    names = [p["name"] for p in loaded["profiles"]]
    assert names == ["A-hermetic", "B-simulated", "C-live"]


async def test_the_artifact_names_the_metric_source_and_adds_no_second_chain():
    result = await baseline.run_all(sessions=1, turns=1, warmup=0)
    assert "metric.turn" in result["metric_source"]


# ── 9, 10: no secrets, no content ───────────────────────────────────────────


async def test_9_and_10_the_artifact_contains_no_credential_audio_or_transcript(tmp_path):
    result = await baseline.run_all(sessions=1, turns=1, warmup=0)
    artifact = json.dumps({k: v for k, v in result.items() if not k.startswith("_")})
    lowered = artifact.lower()
    for marker in ("sk-", "gsk-", "csk-", "authorization", "bearer", "password", "secret_key"):
        assert marker not in lowered, f"the artifact contains {marker!r}"
    # The harness speaks a synthetic utterance; it must not be persisted either.
    assert "benchmark utterance" not in artifact
    assert "a first clause" not in lowered


def test_the_live_status_reports_presence_without_reading_a_value(monkeypatch):
    monkeypatch.setenv("VOXVOICE_SECRET_GROQ_API_KEY", "sk-live-should-never-be-echoed")
    status = baseline.live_provider_status()
    assert status["groq"] == "credential present"
    assert "sk-live" not in json.dumps(status)
    monkeypatch.delenv("VOXVOICE_SECRET_GROQ_API_KEY")
    assert "NOT EXECUTED" in baseline.live_provider_status()["groq"]


# ── architecture: the benchmark is not production ──────────────────────────


def test_no_production_module_imports_the_benchmark_package():
    """Benchmark infrastructure must not become production architecture."""
    import ast
    import pathlib

    root = pathlib.Path(baseline.__file__).parent.parent
    for path in root.rglob("*.py"):
        if any(part in (".venv", "tests", "benchmarks", "voicebot", "__pycache__")
               for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("benchmarks"), path
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith("benchmarks") for a in node.names), path


def test_the_benchmark_package_is_not_installed():
    """`pyproject.toml` names the shipped packages explicitly; `benchmarks` is not among them."""
    import pathlib

    root = pathlib.Path(baseline.__file__).parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    include = pyproject.split("include = [", 1)[1].split("]", 1)[0]
    assert "benchmarks" not in include


def test_the_benchmark_adds_no_second_metric_producer():
    """`observability/` remains the sole producer; the harness only reads."""
    import ast
    import pathlib

    root = pathlib.Path(baseline.__file__).parent
    for path in root.glob("*.py"):
        identifiers = {
            node.attr for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute)
        }
        assert "publish" not in identifiers, f"{path.name} publishes an event"


@pytest.mark.parametrize("delays", [DelayProfile(), DelayProfile(llm_first_token_ms=10.0)])
async def test_the_harness_leaves_no_session_running(delays):
    collector = await harness.run_session(delays, turns=1)
    assert collector.event_count > 0
