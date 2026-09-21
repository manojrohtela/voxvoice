"""Console settings (M12.1, ADR-0055 DP3/DP8).

What the developer-platform application needs *in addition to* the M11 WebRTC application's own
settings (`api.webrtc.config.WebRTCSettings`), which it wraps unchanged. No secret appears here:
the console never names, reads or holds a provider credential (security model S1/S2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Absolute path to a built console bundle (`console/dist`). Unset means the API serves no console.
CONSOLE_DIST_ENV = "VOXVOICE_CONSOLE_DIST"


class ConsoleConfigError(ValueError):
    """The console was configured incompletely or unsafely."""


@dataclass(frozen=True)
class ConsoleSettings:
    #: Directory holding the built console (`index.html` + assets), served under `/console/`.
    #: `None` serves nothing, which keeps a deployment that never asked for a UI free of one.
    dist_dir: Path | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ConsoleSettings:
        source = os.environ if env is None else env
        raw = source.get(CONSOLE_DIST_ENV, "").strip()
        return cls(dist_dir=Path(raw) if raw else None)

    def validate(self) -> None:
        """Refuse a bundle location that cannot be served, at startup rather than per request.

        A configured-but-unusable directory is an error, never a silent "no console" (§A2.6):
        the operator asked for a UI and must learn at once that they will not get one."""
        if self.dist_dir is None:
            return
        if not self.dist_dir.is_absolute():
            raise ConsoleConfigError(f"{CONSOLE_DIST_ENV} must be an absolute path")
        if not self.dist_dir.is_dir():
            raise ConsoleConfigError(f"{CONSOLE_DIST_ENV} is not a directory: {self.dist_dir}")
        if not (self.dist_dir / "index.html").is_file():
            raise ConsoleConfigError(
                f"{CONSOLE_DIST_ENV} has no index.html — run `npm run build` in console/ first"
            )


__all__ = ["CONSOLE_DIST_ENV", "ConsoleConfigError", "ConsoleSettings"]
