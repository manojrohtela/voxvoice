"""Console settings (M12.1–M12.2, ADR-0055 DP3/DP4/DP8).

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

#: Who may create an account through the console (M12.2):
#:   `first_run` — only while no user exists yet: the first local account, then closed (default)
#:   `open`      — anyone who can reach the API
#:   `closed`    — nobody; accounts are created with `python -m api.console.bootstrap_owner`
SIGNUP_MODES = ("first_run", "open", "closed")


class ConsoleConfigError(ValueError):
    """The console was configured incompletely or unsafely."""


def _bool(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    raise ConsoleConfigError(f"{name} must be true or false, not {raw!r}")


def _positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConsoleConfigError(f"{name} must be an integer, not {raw!r}") from exc
    if value <= 0:
        raise ConsoleConfigError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class ConsoleSettings:
    #: Directory holding the built console (`index.html` + assets), served under `/console/`.
    #: `None` serves nothing, which keeps a deployment that never asked for a UI free of one.
    dist_dir: Path | None = None
    signup: str = "first_run"
    #: `Secure` on the session cookie. `None` derives it from `APP_ENV`: off only for `dev`,
    #: because `http://localhost` cannot carry a Secure cookie and everything else must.
    cookie_secure: bool | None = None
    session_absolute_hours: int = 12
    session_idle_minutes: int = 120
    login_max_failures: int = 10
    login_failure_window_minutes: int = 15
    #: Start real-voice sessions with audio-driven endpointing (finding F-7). Applies only to the
    #: WebRTC (real) profile, which registers the voice-activity provider it needs.
    audio_endpointing: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ConsoleSettings:
        source = os.environ if env is None else env
        raw_dist = source.get(CONSOLE_DIST_ENV, "").strip()
        raw_secure = source.get("VOXVOICE_CONSOLE_COOKIE_SECURE", "").strip()
        raw_endpointing = source.get("VOXVOICE_CONSOLE_AUDIO_ENDPOINTING", "").strip()
        defaults = cls()
        return cls(
            dist_dir=Path(raw_dist) if raw_dist else None,
            signup=source.get("VOXVOICE_CONSOLE_SIGNUP", defaults.signup).strip(),
            cookie_secure=(
                _bool(raw_secure, "VOXVOICE_CONSOLE_COOKIE_SECURE") if raw_secure else None
            ),
            audio_endpointing=(
                _bool(raw_endpointing, "VOXVOICE_CONSOLE_AUDIO_ENDPOINTING")
                if raw_endpointing
                else defaults.audio_endpointing
            ),
            session_absolute_hours=_positive_int(
                source.get("VOXVOICE_CONSOLE_SESSION_HOURS", str(defaults.session_absolute_hours)),
                "VOXVOICE_CONSOLE_SESSION_HOURS",
            ),
            session_idle_minutes=_positive_int(
                source.get(
                    "VOXVOICE_CONSOLE_SESSION_IDLE_MINUTES", str(defaults.session_idle_minutes)
                ),
                "VOXVOICE_CONSOLE_SESSION_IDLE_MINUTES",
            ),
            login_max_failures=_positive_int(
                source.get("VOXVOICE_CONSOLE_LOGIN_MAX_FAILURES", str(defaults.login_max_failures)),
                "VOXVOICE_CONSOLE_LOGIN_MAX_FAILURES",
            ),
            login_failure_window_minutes=_positive_int(
                source.get(
                    "VOXVOICE_CONSOLE_LOGIN_WINDOW_MINUTES",
                    str(defaults.login_failure_window_minutes),
                ),
                "VOXVOICE_CONSOLE_LOGIN_WINDOW_MINUTES",
            ),
        )

    def validate(self) -> None:
        """Refuse an unusable configuration at startup rather than per request (§A2.6)."""
        if self.signup not in SIGNUP_MODES:
            raise ConsoleConfigError(
                f"VOXVOICE_CONSOLE_SIGNUP must be one of {SIGNUP_MODES}, not {self.signup!r}"
            )
        for name in (
            "session_absolute_hours",
            "session_idle_minutes",
            "login_max_failures",
            "login_failure_window_minutes",
        ):
            if getattr(self, name) <= 0:
                raise ConsoleConfigError(f"{name} must be positive")
        if self.session_idle_minutes > self.session_absolute_hours * 60:
            raise ConsoleConfigError("the idle timeout cannot exceed the absolute session lifetime")
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

    def secure_cookie(self, app_env: str) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else app_env != "dev"


__all__ = ["CONSOLE_DIST_ENV", "SIGNUP_MODES", "ConsoleConfigError", "ConsoleSettings"]
