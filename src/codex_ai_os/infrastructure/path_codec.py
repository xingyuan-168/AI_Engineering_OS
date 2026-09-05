"""Unicode-safe transport for absolute paths stored in runtime state."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

REPLACEMENT_CHARACTER = "\ufffd"


class PathEncodingError(ValueError):
    code = "PATH_ENCODING_CORRUPT"


def state_path(value: str | Path) -> str:
    """Return a resolved portable path and reject already-corrupted Unicode."""

    raw = str(value)
    require_valid_unicode(raw)
    normalized = Path(raw).resolve(strict=False).as_posix()
    require_valid_unicode(normalized)
    return normalized


def path_from_state(value: object) -> Path:
    text = str(value)
    require_valid_unicode(text)
    return Path(text)


def require_valid_unicode(value: str) -> None:
    if REPLACEMENT_CHARACTER in value:
        raise PathEncodingError("path contains Unicode replacement character U+FFFD")


def configure_utf8_stdio() -> None:
    """Make console and MCP JSON transport deterministic on Windows."""

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        _reconfigure_utf8(stream)


def _reconfigure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")


__all__ = [
    "PathEncodingError",
    "configure_utf8_stdio",
    "path_from_state",
    "require_valid_unicode",
    "state_path",
]
