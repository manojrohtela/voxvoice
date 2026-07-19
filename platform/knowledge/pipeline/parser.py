"""Local default parser (M5.2B-1) — plain text, deterministic, no vendor library.

`PlainTextParser` decodes raw bytes as UTF-8 (replacing undecodable bytes). Real format
parsers (PDF/HTML/DOCX) implement the same `Parser` port and swap in as adapters — the
runtime never parses inline.
"""

from __future__ import annotations

from ..ports import Parser


class PlainTextParser(Parser):
    name = "plain_text"

    def parse(self, raw: bytes, *, mime: str) -> str:
        return raw.decode("utf-8", errors="replace")
