"""SemVer helpers for the compiler (M7.0B-2) — pure, deterministic, no deps.

Version *format* validation lives in `product.validation`; this module adds the
*ordering* and *compatibility* comparisons the compiler needs (highest-version
selection during binding resolution; static platform-compatibility checks). Build
metadata is ignored for ordering (SemVer 2.0.0 §10); a pre-release ranks below its
corresponding release.
"""

from __future__ import annotations

import re

_SEMVER = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def parse_semver(version: str) -> tuple[int, int, int, str] | None:
    """Return (major, minor, patch, prerelease) or None if malformed."""
    m = _SEMVER.match(version)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")


def version_sort_key(version: str) -> tuple[int, int, int, int, str]:
    """A total-order key for selecting the highest version. A release (empty
    prerelease) outranks its pre-releases; malformed versions sort lowest."""
    parsed = parse_semver(version)
    if parsed is None:
        return (-1, -1, -1, -1, version)
    major, minor, patch, pre = parsed
    # release (pre == "") must rank ABOVE any pre-release -> flag 1 vs 0.
    return (major, minor, patch, 1 if pre == "" else 0, pre)


def highest_version(versions: tuple[str, ...]) -> str | None:
    """The highest well-formed version, or None if the input is empty."""
    if not versions:
        return None
    return max(versions, key=version_sort_key)


def is_compatible(required: str, actual: str) -> bool:
    """Static compatibility: `actual` satisfies `required` when it shares the major
    version and is >= it (a caret-style, same-major floor). An empty `required`
    means "no constraint". Malformed inputs are NOT compatible — the caller reports
    the malformed constraint separately."""
    if not required:
        return True
    req = parse_semver(required)
    act = parse_semver(actual)
    if req is None or act is None:
        return False
    if req[0] != act[0]:
        return False
    return version_sort_key(actual) >= version_sort_key(required)
