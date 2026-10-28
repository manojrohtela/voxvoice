"""Operational documentation may not instruct a host-wide ephemeral-port change (M13.2).

The Stage-2 runbook once told operators to run
`sysctl -w net.ipv4.ip_local_port_range="50000 50100"` so the media firewall rule would be small.
That setting is **host-wide**: on a shared host it re-pools the ephemeral ports of every other
service on the machine. DL-0258 prohibits it outright, and M13.2 made it unnecessary —
`VOXVOICE_MEDIA_UDP_PORT_RANGE` bounds VoxVoice's own media sockets in-process. DL-0259 records
the correction. Nothing mechanically stopped the instruction from returning, which is what this
file is for.

## Why it reads fenced code only

The distinction that matters is **runnable instruction** versus **prose**. The withdrawn text was
a fence an operator would copy; the replacement is a prose prohibition that necessarily mentions
the same setting. A substring scan cannot tell them apart and would forbid the document from
explaining its own rule — the trap `test_application.py`'s literal search for `"application"` set
for M13.1. So only fenced lines are scanned, and prose may discuss the setting freely. Reading it
stays legitimate: `sysctl net.ipv4.ip_local_port_range` and `cat /proc/sys/...` are how the
baseline is taken and must remain usable in a fence.

## What the fence reader does and does not cover

It tracks **backtick and tilde** fences, requires the closing marker to use the same character as
the opening one (CommonMark's rule, and the only part of it this needs), and tolerates a
**blockquote prefix**, because the runbook's own prohibition puts its replacement example inside
a `>` block.

It is deliberately **not** a Markdown parser, and it does **not** cover every code-block form:
**four-space indented code blocks are not scanned**, nor are HTML `<pre>` blocks, nor fences
nested inside a fence of the same character. A write instruction written in one of those forms
would evade this control. The claim here is narrow — that the form the runbook actually used, and
the forms an operator would plausibly copy, are caught — not that the scan is exhaustive.
"""

from __future__ import annotations

import re
from pathlib import Path

from providers.transport.ice import parse_media_port_range

REPO = Path(__file__).resolve().parents[2]
OPERATIONS = REPO / "docs" / "operations"
RUNBOOK = OPERATIONS / "stage2-voice-host-runbook.md"

#: The measured baseline on the AgentHive hub, which DL-0258 requires be preserved.
BASELINE = "32768 60999"

#: Writes to the host-wide ephemeral range, in every spelling an operational document might use.
#: Each requires an actual mutation — a `-w`/`--write` flag, an assignment, or a redirect into
#: `/proc/sys`. Nothing here matches a bare read.
_PROHIBITED_WRITES = (
    re.compile(r"\bsysctl\b[^\n]*\s(-w|--write)\b[^\n]*ip_local_port_range", re.IGNORECASE),
    re.compile(r"\bsysctl\b[^\n]*ip_local_port_range\s*=", re.IGNORECASE),
    re.compile(r"\bip_local_port_range\s*=", re.IGNORECASE),  # /etc/sysctl.d persistence
    re.compile(r"(>|>>|\btee\b)[^\n]*/proc/sys/net/ipv4/ip_local_port_range"),
)

#: A fence opener or closer, optionally inside a blockquote.
_FENCE = re.compile(r"^[>\s]*(`{3,}|~{3,})")

#: A Markdown blockquote prefix, which must be removed before shell patterns are applied: the
#: `>` of `> cat /proc/sys/...` is quoting syntax, not a redirect, and matching it as one flagged
#: a legitimate read the first time this file ran.
_BLOCKQUOTE = re.compile(r"^\s*(?:>\s?)+")


def _fenced_lines(markdown: str):
    """Yield `(line_number, content)` for lines inside a fenced block, blockquote prefix removed.

    Backtick and tilde fences, blockquoted or not. The closing marker must use the same character
    as the opening one, so a tilde fence is not closed by backticks or vice versa. `content` is
    what a reader would copy into a shell, which is what the write patterns must see."""
    marker: str | None = None
    for number, line in enumerate(markdown.splitlines(), 1):
        opened = _FENCE.match(line)
        if marker is None:
            if opened:
                marker = opened.group(1)[0]
            continue
        if opened and opened.group(1)[0] == marker:
            marker = None
            continue
        yield number, _BLOCKQUOTE.sub("", line)


def _offenders(markdown: str) -> list[tuple[int, str]]:
    return [
        (number, line.strip())
        for number, line in _fenced_lines(markdown)
        if any(pattern.search(line) for pattern in _PROHIBITED_WRITES)
    ]


def _section(markdown: str, heading: str) -> str:
    """The text of one `## <heading>` section, up to the next `## `."""
    start = markdown.index(f"\n## {heading}")
    rest = markdown[start + 1 :]
    end = rest.find("\n## ", 1)
    return rest if end == -1 else rest[:end]


# ══ the prohibition ═══════════════════════════════════════════════════════════


def test_no_operational_document_instructs_a_host_wide_port_range_change():
    """DL-0258: the setting must never be modified, so no runnable instruction may say otherwise."""
    assert OPERATIONS.is_dir(), OPERATIONS
    documents = sorted(OPERATIONS.rglob("*.md"))
    assert documents, "no operational documents found; the scan would be vacuous"
    offenders = {
        path.relative_to(REPO).as_posix(): hits
        for path in documents
        if (hits := _offenders(path.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        "operational documentation contains a runnable host-wide ephemeral-port change; "
        f"DL-0258 forbids it and VOXVOICE_MEDIA_UDP_PORT_RANGE replaces it: {offenders}"
    )


def test_the_detector_catches_every_prohibited_spelling_in_both_fence_styles():
    """Not vacuous, and not backtick-only: each write form is caught in every fence style used."""
    commands = (
        'sudo sysctl -w net.ipv4.ip_local_port_range="50000 50100"',
        "sysctl --write net.ipv4.ip_local_port_range='50000 50100'",
        "sysctl net.ipv4.ip_local_port_range=50000 50100",
        "net.ipv4.ip_local_port_range = 50000 50100",
        'echo "50000 50100" > /proc/sys/net/ipv4/ip_local_port_range',
        'echo "50000 50100" >> /proc/sys/net/ipv4/ip_local_port_range',
        'echo "50000 50100" | tee /proc/sys/net/ipv4/ip_local_port_range',
    )
    for command in commands:
        assert _offenders(f"```bash\n{command}\n```"), f"backtick fence missed: {command}"
        assert _offenders(f"~~~bash\n{command}\n~~~"), f"tilde fence missed: {command}"
        assert _offenders(f"````\n{command}\n````"), f"long fence missed: {command}"
        assert _offenders(f"> ```\n> {command}\n> ```"), f"blockquote fence missed: {command}"


def test_a_tilde_fence_is_not_closed_by_backticks():
    """Mismatched markers must not end the block early, or a write could hide after them."""
    markdown = '~~~\n```\nsudo sysctl -w net.ipv4.ip_local_port_range="1 2"\n~~~\n'
    assert _offenders(markdown)


def test_the_detector_permits_read_only_inspection():
    """Reading the value is how the baseline is taken, and must stay usable in a fence."""
    for command in (
        "sysctl net.ipv4.ip_local_port_range",
        "sysctl -n net.ipv4.ip_local_port_range",
        "cat /proc/sys/net/ipv4/ip_local_port_range",
        "grep . /proc/sys/net/ipv4/ip_local_port_range",
    ):
        assert not _offenders(f"```bash\n{command}\n```"), f"read flagged: {command}"
        assert not _offenders(f"~~~bash\n{command}\n~~~"), f"read flagged: {command}"
        assert not _offenders(f"> ```\n> {command}\n> ```"), f"read flagged: {command}"


def test_a_blockquote_marker_is_not_mistaken_for_a_shell_redirect():
    """The false positive this file hit on its first run, pinned.

    `> cat /proc/sys/net/ipv4/ip_local_port_range` inside a blockquoted fence is a **read**; the
    leading `>` is Markdown quoting, not a redirect. Matching it as one flagged a legitimate
    baseline command, so the blockquote prefix is stripped before the shell patterns apply — while
    a genuine redirect inside a blockquote is still caught."""
    read = "> ```\n> cat /proc/sys/net/ipv4/ip_local_port_range\n> ```"
    assert not _offenders(read), "a blockquoted read was flagged as a write"
    write = '> ```\n> echo "1 2" > /proc/sys/net/ipv4/ip_local_port_range\n> ```'
    assert _offenders(write), "a blockquoted redirect escaped the scan"


def test_prose_may_state_the_prohibition():
    """A document must be able to explain its own rule without tripping the scan.

    This is the failure mode a substring search would have: the correction's own wording mentions
    the setting, and inline backticks are not a fence."""
    prose = (
        "> **Do not write this setting** — not with `sysctl`, not through `/proc/sys`, and not by\n"
        "> persisting it in `/etc/sysctl.d/`. The measured baseline is `32768 60999` and it must\n"
        "> remain exactly that (DL-0258). Reading it is fine; changing it is not.\n"
    )
    assert not _offenders(prose)


def test_the_indented_code_block_limitation_is_real_and_documented():
    """Pins the gap the module docstring declares, so the claim stays honest.

    A four-space indented block is not scanned. This asserts that known hole rather than letting
    a reader assume the scan is exhaustive — if a future reader closes it, this test fails and the
    docstring gets corrected with it."""
    indented = '    sudo sysctl -w net.ipv4.ip_local_port_range="50000 50100"\n'
    assert not _offenders(indented), (
        "indented code blocks are now scanned; update this module's docstring, which still "
        "declares them out of scope"
    )


# ══ the replacement guidance ══════════════════════════════════════════════════


def test_the_runbook_keeps_the_replacement_guidance_and_the_preserved_baseline():
    """Removing the bad instruction is only half the correction (DL-0259).

    Asserted on operational invariants, not sentences: the variable an operator must set, and the
    baseline value DL-0258 requires be preserved."""
    section = _section(RUNBOOK.read_text(encoding="utf-8"), "3.")
    assert "VOXVOICE_MEDIA_UDP_PORT_RANGE" in section, (
        "§3 no longer names the in-process replacement for the prohibited sysctl"
    )
    assert BASELINE in section, f"§3 no longer records the preserved baseline {BASELINE!r}"


def test_the_runbook_documents_half_open_semantics_consistent_with_the_parser():
    """The documented range is parsed by the **real** M13.2 parser, not re-described here.

    `51000-51008` is half-open, so the inclusive ports are 51000-51007. If the documentation
    stated an inclusive upper bound, an operator would open one port too few and the last call of
    a busy window would fail to gather."""
    section = _section(RUNBOOK.read_text(encoding="utf-8"), "3.")
    match = re.search(r"VOXVOICE_MEDIA_UDP_PORT_RANGE=(\d+-\d+)", section)
    assert match, "§3 shows no concrete VOXVOICE_MEDIA_UDP_PORT_RANGE value"
    lo, hi = parse_media_port_range(match.group(1))
    assert f"{lo}-{hi - 1}" in section, (
        f"§3 documents the range {match.group(1)!r} but never states its inclusive ports "
        f"{lo}-{hi - 1}; half-open semantics are not conveyed"
    )
