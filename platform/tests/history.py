"""Historical-evidence helpers for the freeze/certification suites (M11.2.1, ADR-0052).

A certification is a statement about **a tree that was certified**, not about whoever last edited
the checkout. Most of that question is answerable with `git diff A B` / `git ls-tree` over two
pinned commits, and the freeze suites do exactly that.

Two claims cannot be answered that way, because they require *executing* the certification rather
than reading files: *"does the certified tree still produce the certified digest?"* and *"does the
released tree still produce the released manifest digest?"* Recomputing those against the working
tree answers a different question — the digests move the moment any later epic adds a module,
which is what happened at M11.2 when two new files under `runtime/pipeline/audio/` moved Epic 9's
`PL-2` examined count from 256 to 258 and with it the whole report digest.

So this module materializes the historical tree in a detached `git worktree` and runs the
certification **there**. That keeps the assertion a real recomputation — it cannot be made green
by weakening it — while making it a question about history, which is the only question a freeze is
entitled to ask.

Hermetic: no network, no vendor, no credentials, no sleeps. The worktree is created at most once
per process, under the system temp directory, and removed at exit.
"""

from __future__ import annotations

import atexit
import functools
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The newest released tree. Every "the released state still reproduces its recorded digest"
#: assertion is anchored here. **A release milestone advances this pin**, exactly as `v4.0.0`'s
#: pins were advanced when later work re-baselined them — it is not epic-specific machinery.
RELEASED_TAG = "v5.0.0"
RELEASED_COMMIT = "3779e932182ed06ec5411c83ba53f0c2b585b8b8"


class HistoricalEvidenceUnavailable(RuntimeError):
    """The historical tree could not be materialized, so the evidence cannot be read."""


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd or REPO_ROOT, check=False
    ).stdout.strip()


@functools.lru_cache(maxsize=4)
def historical_worktree(commit: str) -> Path:
    """A read-only checkout of `commit`, created once per process and removed at exit.

    Detached, so it can never move a branch, and placed outside the repository so it can never be
    mistaken for working-tree content."""
    resolved = git("rev-parse", f"{commit}^{{commit}}")
    if resolved != commit:
        raise HistoricalEvidenceUnavailable(f"{commit} does not resolve to itself ({resolved!r})")

    path = Path(tempfile.mkdtemp(prefix="voxvoice-history-"))
    target = path / "tree"
    result = subprocess.run(
        ["git", "worktree", "add", "--detach", str(target), commit],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    if result.returncode != 0:  # pragma: no cover — environment failure
        shutil.rmtree(path, ignore_errors=True)
        raise HistoricalEvidenceUnavailable(
            f"could not materialize {commit}: {result.stderr.strip()}"
        )

    def _cleanup() -> None:  # pragma: no cover — process teardown
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(target)],
            capture_output=True, cwd=REPO_ROOT, check=False,
        )
        shutil.rmtree(path, ignore_errors=True)

    atexit.register(_cleanup)
    return target


def evaluate_at(commit: str, expression: str) -> object:
    """Evaluate `expression` inside the historical checkout of `commit` and return its JSON value.

    Runs in a **subprocess** rooted at that tree's `platform/`, so the imported modules and every
    path they walk are the historical ones — importing them into this process would read the
    current tree and defeat the purpose. The interpreter is this one, so no environment differs."""
    tree = historical_worktree(commit)
    platform_root = tree / "platform"
    program = (
        "import sys, json;"
        "sys.path.insert(0, '.');"
        f"print(json.dumps({expression}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=platform_root, check=False,
    )
    if result.returncode != 0:
        raise HistoricalEvidenceUnavailable(
            f"evaluating {expression!r} at {commit} failed: {result.stderr.strip()[-800:]}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


__all__ = [
    "HistoricalEvidenceUnavailable",
    "RELEASED_COMMIT",
    "RELEASED_TAG",
    "REPO_ROOT",
    "evaluate_at",
    "git",
    "historical_worktree",
]
