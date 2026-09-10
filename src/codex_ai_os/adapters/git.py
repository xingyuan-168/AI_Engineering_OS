"""Single internal Git subprocess wrapper shared across the runtime."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GitRunner:
    """Run Git commands in one repository without shell interpolation."""

    root: Path

    def run(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
        )

    def run_bytes(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
