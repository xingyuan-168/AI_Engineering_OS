"""Repository governance value objects and lexical path rules (ADR-0016).

The heavy evidence, review, release, and memory lifecycle models were
removed with the governance-core refactor; only the repository check
report and the shared lexical path rules remain.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from codex_ai_os.domain.config import StrictModel


class RepositoryFinding(StrictModel):
    code: str
    severity: str = "error"
    message: str
    path: str | None = None
    blocking: bool = True


class RepositoryCheckReport(StrictModel):
    repository_ready: bool
    mode: str
    root: str
    head_commit: str | None = None
    remote_host: str | None = None
    hygiene_ok: bool
    findings: tuple[RepositoryFinding, ...]


def is_unsafe_repository_path(value: str) -> bool:
    """Apply Windows and POSIX lexical rules regardless of the runtime host."""

    return (
        not value
        or PurePosixPath(value) == PurePosixPath(".")
        or PurePosixPath(value).is_absolute()
        or bool(PureWindowsPath(value).drive)
        or PureWindowsPath(value).is_reserved()
        or ":" in value  # Also excludes NTFS alternate data streams.
        or "\ufffd" in value
        or any(ord(character) < 32 for character in value)
        or any(
            part == ".." or (part not in {"", "."} and part.endswith((".", " ")))
            for part in value.split("/")
        )
    )


def governed_path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    """Project-relative allowance used by the authorization kernel (ADR-0011).

    A path is allowed when it equals an allowed entry or lies below it.
    Absolute paths, ".." segments and drive-qualified paths are never
    allowed.
    """

    normalized = path.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in posix.parts:
        return False
    if is_unsafe_repository_path(normalized):
        return False
    candidate = posix.as_posix()
    for raw_allowed in allowed_paths:
        allowed = PurePosixPath(raw_allowed.replace("\\", "/")).as_posix()
        if candidate == allowed or candidate.startswith(f"{allowed.rstrip('/')}/"):
            return True
    return False


__all__ = [
    "RepositoryCheckReport",
    "RepositoryFinding",
    "governed_path_allowed",
    "is_unsafe_repository_path",
]
