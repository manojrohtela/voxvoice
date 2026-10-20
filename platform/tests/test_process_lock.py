"""The single-process lock: contention, release, and what the kernel does for us (M13.1).

The call slot and the rendezvous registry are both per process, so two workers would hold two
independent caps and two independent registries. This lock makes that configuration fail loudly at
startup instead of silently doubling the capacity an operator thought they had set.

The test that matters most is the one that kills a process without letting it clean up: an
advisory `flock` belongs to the open file description, so the kernel releases it on *any* exit,
including `SIGKILL`. If that were not true, every crash would leave a lock file that prevents
restart — which is the failure mode this design exists to avoid.

Nothing here sleeps for a duration, and the subprocess is killed by its own pid, never by name.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys

import pytest

from api.db import init_db
from api.webrtc.app import build_webrtc_app
from api.webrtc.config import WebRTCSettings
from api.webrtc.process_lock import (
    LOCK_PATH_ENV,
    SingleProcessLockHeld,
    acquire_single_process_lock,
    lock_path_from_env,
    release_single_process_lock,
)

DEADLINE_S = 10.0

#: Acquires the lock, announces it, and then waits to be killed without releasing anything.
HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o640)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
sys.stdout.write("held\\n")
sys.stdout.flush()
sys.stdin.read()
"""


# ══ the lock itself ═══════════════════════════════════════════════════════════


def test_the_lock_is_exclusive_within_one_process(tmp_path):
    path = str(tmp_path / "single.lock")
    fd = acquire_single_process_lock(path)
    try:
        with pytest.raises(SingleProcessLockHeld, match="refusing to start a second worker"):
            acquire_single_process_lock(path)
    finally:
        release_single_process_lock(fd)


def test_releasing_makes_it_available_again(tmp_path):
    path = str(tmp_path / "single.lock")
    fd = acquire_single_process_lock(path)
    release_single_process_lock(fd)
    again = acquire_single_process_lock(path)
    release_single_process_lock(again)


def test_the_holder_s_pid_is_recorded_for_operators(tmp_path):
    path = str(tmp_path / "single.lock")
    fd = acquire_single_process_lock(path)
    try:
        assert open(path).read().strip() == str(os.getpid())
    finally:
        release_single_process_lock(fd)


def test_the_file_is_created_with_restrictive_permissions(tmp_path):
    path = str(tmp_path / "single.lock")
    fd = acquire_single_process_lock(path)
    try:
        assert os.stat(path).st_mode & 0o777 == 0o640
    finally:
        release_single_process_lock(fd)


def test_an_existing_lock_file_is_reused_rather_than_refused(tmp_path):
    """A leftover file is not a leftover lock — only a live descriptor excludes."""
    path = tmp_path / "single.lock"
    path.write_text("99999\n")
    fd = acquire_single_process_lock(str(path))
    release_single_process_lock(fd)


def test_the_operating_system_releases_the_lock_when_a_process_is_killed(tmp_path):
    """`SIGKILL` gives the holder no chance to clean up, and none is needed."""
    path = str(tmp_path / "single.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(SingleProcessLockHeld):
            acquire_single_process_lock(path)
        holder.kill()  # this process, by its own pid — never by name
        holder.wait(timeout=DEADLINE_S)
    finally:
        if holder.poll() is None:  # pragma: no cover — only if the kill above failed
            holder.kill()
            holder.wait(timeout=DEADLINE_S)
    fd = acquire_single_process_lock(path)
    release_single_process_lock(fd)


# ══ configuration ═════════════════════════════════════════════════════════════


def test_the_lock_is_disabled_unless_a_path_is_configured(monkeypatch):
    monkeypatch.delenv(LOCK_PATH_ENV, raising=False)
    assert lock_path_from_env() is None
    monkeypatch.setenv(LOCK_PATH_ENV, "   ")
    assert lock_path_from_env() is None
    monkeypatch.setenv(LOCK_PATH_ENV, " /tmp/x.lock ")
    assert lock_path_from_env() == "/tmp/x.lock"


# ══ the application lifespan ══════════════════════════════════════════════════


async def test_the_application_holds_the_lock_while_it_runs(monkeypatch, tmp_path):
    path = str(tmp_path / "app.lock")
    monkeypatch.setenv(LOCK_PATH_ENV, path)
    init_db()
    app = build_webrtc_app(WebRTCSettings())
    async with app.router.lifespan_context(app):
        assert app.state.single_process_lock_fd is not None
        with pytest.raises(SingleProcessLockHeld):
            acquire_single_process_lock(path)
    assert app.state.single_process_lock_fd is None
    fd = acquire_single_process_lock(path)  # released on shutdown
    release_single_process_lock(fd)


async def test_a_second_application_refuses_to_start(monkeypatch, tmp_path):
    """The configuration this exists to catch: a second worker on the same host."""
    path = str(tmp_path / "app.lock")
    monkeypatch.setenv(LOCK_PATH_ENV, path)
    init_db()
    first = build_webrtc_app(WebRTCSettings())
    second = build_webrtc_app(WebRTCSettings())
    async with first.router.lifespan_context(first):
        with pytest.raises(SingleProcessLockHeld):
            async with second.router.lifespan_context(second):
                pytest.fail("the second application started while the first held the lock")


async def test_the_lock_is_released_when_the_application_fails_after_startup(
    monkeypatch, tmp_path
):
    """A failure inside the running application must not keep the lock out of circulation."""
    path = str(tmp_path / "app.lock")
    monkeypatch.setenv(LOCK_PATH_ENV, path)
    init_db()
    app = build_webrtc_app(WebRTCSettings())
    with contextlib.suppress(RuntimeError):
        async with app.router.lifespan_context(app):
            raise RuntimeError("something failed while serving")
    fd = acquire_single_process_lock(path)
    release_single_process_lock(fd)


async def test_without_the_variable_no_lock_file_is_created(monkeypatch, tmp_path):
    """A deployment that never asked for this gets no new way to fail to start."""
    monkeypatch.delenv(LOCK_PATH_ENV, raising=False)
    init_db()
    app = build_webrtc_app(WebRTCSettings())
    async with app.router.lifespan_context(app):
        assert app.state.single_process_lock_fd is None
    assert list(tmp_path.iterdir()) == []


async def test_the_lock_does_not_interfere_with_a_restart(monkeypatch, tmp_path):
    """systemd stops the old process before starting the new one; the lock must follow suit."""
    path = str(tmp_path / "app.lock")
    monkeypatch.setenv(LOCK_PATH_ENV, path)
    init_db()
    for _ in range(3):
        app = build_webrtc_app(WebRTCSettings())
        async with app.router.lifespan_context(app):
            assert app.state.single_process_lock_fd is not None
        await asyncio.sleep(0)
