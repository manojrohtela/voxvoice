"""Serve the built console bundle under `/console/` (M12.1, ADR-0055 DP8).

Local product mode runs the whole platform from one process. The console is a single-page app,
so any path that is not a real file is answered with `index.html` and routed in the browser —
except a path that *names* a file (it has an extension), which is a 404: answering a missing
script with HTML turns a clear error into a confusing one.

Every requested path is resolved and must stay inside the bundle directory; anything that
escapes it is a 404 (security model §6, path traversal).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from .errors import NotFound

CONSOLE_PATH = "/console"


def mount_console_bundle(app: FastAPI, dist_dir: Path) -> None:
    root = dist_dir.resolve()
    index = root / "index.html"

    @app.get(CONSOLE_PATH, include_in_schema=False)
    def console_root() -> RedirectResponse:
        return RedirectResponse(f"{CONSOLE_PATH}/")

    @app.get(CONSOLE_PATH + "/{path:path}", include_in_schema=False)
    def console_file(path: str) -> FileResponse:
        if path:
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                raise NotFound("Not found")
            if candidate.is_file():
                return FileResponse(candidate)
            if PurePosixPath(path).suffix:
                raise NotFound("Not found")
        # The shell must never be cached: it names the hashed assets of the current build.
        return FileResponse(index, headers={"cache-control": "no-cache"})


__all__ = ["CONSOLE_PATH", "mount_console_bundle"]
