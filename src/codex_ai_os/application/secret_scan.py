"""Scan every Git-tracked file and fail when detect-secrets reports a finding."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings


class SecretScanError(RuntimeError):
    """Raised when the repository cannot be scanned deterministically."""


def scan_repository(root: Path) -> dict[str, Any]:
    repository = root.resolve()
    tracked = _tracked_files(repository)
    findings: list[dict[str, object]] = []
    with default_settings():
        secrets = SecretsCollection(root=str(repository))
        for relative in tracked:
            secrets.scan_file(relative)
        serialized = cast(dict[str, list[dict[str, object]]], secrets.json())
    for filename, entries in sorted(serialized.items()):
        for entry in entries:
            findings.append(
                {
                    "filename": filename,
                    "line_number": entry.get("line_number"),
                    "type": entry.get("type"),
                }
            )
    return {
        "valid": not findings,
        "tracked_files": len(tracked),
        "finding_count": len(findings),
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    try:
        result = scan_repository(arguments.repository)
    except (OSError, UnicodeError, SecretScanError) as exc:
        print(f"secret scan failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["valid"] else 1


def _tracked_files(root: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SecretScanError(detail or "cannot enumerate Git-tracked files")
    files = tuple(path for path in completed.stdout.decode("utf-8").split("\0") if path)
    if not files:
        raise SecretScanError("repository has no Git-tracked files")
    return files


if __name__ == "__main__":  # pragma: no cover - exercised by the sandbox integration
    raise SystemExit(main())
