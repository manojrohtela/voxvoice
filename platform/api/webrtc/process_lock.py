"""Refusing to start a second application process (M13.1).

## What this is for, and what it is emphatically not

`call_slot.py` and `RendezvousRegistry` both hold their state in **one interpreter's memory**. Two
uvicorn workers therefore hold two independent call slots and two independent registries, so a cap
of one call admits two, and a rendezvous reserved by one worker is unknown to the other
(`R-E12-5`). One process is a correctness precondition for both, and until now nothing enforced
it: a stray `--workers 2`, a second unit, or a manual run alongside the service would silently
double the capacity the operator thought they had configured.

An advisory `flock` makes that failure **loud and immediate** instead: the second process refuses
to start rather than quietly disagreeing with the first about how many calls are in progress.

**It is not distributed coordination.** It says nothing across hosts, and nothing about which
process should be running — only that a second one on this machine will not start while the first
holds the file. Anything that needs real cross-process agreement needs a design this is not.

## Why `flock` and not a pid file

The lock belongs to the **open file description**, so the kernel releases it when the last
descriptor closes — including when the process is killed, crashes or is OOM-killed. There is no
stale lock to clean up and no pid to validate, which is exactly the failure mode that makes pid
files unreliable precisely when something has gone wrong.

## Off unless configured

`VOXVOICE_SINGLE_PROCESS_LOCK` names the file. Unset, nothing is locked and startup is byte-for-
byte what it was: a deployment that never asked for this gets no new way to fail to start. The
Stage-2 runbook sets it to a path inside the service's writable state directory.
"""

from __future__ import annotations

import fcntl
import logging
import os

log = logging.getLogger("voxvoice.api.webrtc.process_lock")

#: The lock file's path. Unset or empty disables the check entirely.
LOCK_PATH_ENV = "VOXVOICE_SINGLE_PROCESS_LOCK"


class SingleProcessLockHeld(RuntimeError):
    """Another process already holds the lock, so this one must not run.

    A `RuntimeError` rather than a configuration error: the configuration is fine — the situation
    is not."""


def lock_path_from_env(env: dict[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    return (source.get(LOCK_PATH_ENV) or "").strip() or None


def acquire_single_process_lock(path: str) -> int:
    """Take the lock, or raise `SingleProcessLockHeld`. Returns the descriptor holding it.

    The descriptor must stay open for as long as the process should keep the lock — closing it,
    or letting it be garbage-collected, releases it. The caller owns it and passes it to
    `release_single_process_lock` on shutdown.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise SingleProcessLockHeld(
            f"another process holds {path}; refusing to start a second worker "
            "(the call slot and the rendezvous registry are per process)"
        ) from exc
    # The pid is written for operators reading the file during an incident. It is a diagnostic,
    # never the lock itself: the kernel's flock is what excludes, and it needs no content at all.
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:  # pragma: no cover — a lock we hold but cannot annotate is still a lock
        log.warning("could not record the pid in %s; the lock is held regardless", path)
    log.info("single-process lock held: %s (pid %d)", path, os.getpid())
    return fd


def release_single_process_lock(fd: int) -> None:
    """Give the lock back. Idempotent enough to be safe in a `finally`."""
    try:
        os.close(fd)
    except OSError:  # pragma: no cover — already closed; the lock is gone either way
        pass


__all__ = [
    "LOCK_PATH_ENV",
    "SingleProcessLockHeld",
    "acquire_single_process_lock",
    "lock_path_from_env",
    "release_single_process_lock",
]
