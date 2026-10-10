"""Incremental secret scan for the current change (P0-8, promoted per rule 8).

Usage:
    python scripts/secret_scan_incremental.py <file> [<file> ...]

Scans only the files passed on the command line (the change under commit)
with detect-secrets and prints one JSON line:

    {"valid": true, "findings": []}          - clean, exit 0
    {"valid": false, "findings": [...]}      - secrets found, exit 1
    {"valid": false, "error": "..."}         - scan failed to run, exit 2
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _findings(files: list[str], *, staged: bool = False) -> list[dict[str, object]]:
    from detect_secrets.core.scan import scan_file
    from detect_secrets.settings import default_settings

    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="aios-secret-scan-") as snapshot, default_settings():
        for filename in files:
            if staged:
                result = subprocess.run(
                    ["git", "show", ":" + filename], capture_output=True, check=True
                )
                # scan_line is an eager, ad-hoc API, not file scanning: it bypasses
                # entropy thresholds. Use scan_file on an exact isolated index snapshot.
                target = (Path(snapshot) / filename).resolve()
                if not target.is_relative_to(Path(snapshot).resolve()):
                    raise ValueError("staged paths must be repository relative")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(result.stdout.decode("utf-8"), encoding="utf-8")
                scan_path = str(target)
            else:
                # scan_file intentionally skips unreadable/missing files; preflight is mandatory.
                Path(filename).read_text(encoding="utf-8")
                scan_path = filename
            for secret in scan_file(scan_path):
                results.append(
                    {
                        "file": filename,
                        "type": getattr(secret, "type", "unknown"),
                        "line_number": getattr(secret, "line_number", 0),
                    }
                )
    return results


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    files = [name for name in argv if name.strip() and name != "--staged"]
    try:
        if staged and not files:
            changed = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                capture_output=True,
                check=True,
            )
            files = [name for name in changed.stdout.decode("utf-8").split("\0") if name]
        findings = _findings(files, staged=staged)
    except Exception as exc:  # fail closed: an unusable scan is not a pass
        print(json.dumps({"valid": False, "error": f"secret scan failed: {exc}"}))
        return 2
    print(json.dumps({"valid": not findings, "findings": findings}))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
