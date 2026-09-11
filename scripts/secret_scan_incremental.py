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
import sys


def _findings(files: list[str]) -> list[dict[str, object]]:
    from detect_secrets.core.scan import scan_file
    from detect_secrets.settings import default_settings

    results: list[dict[str, object]] = []
    with default_settings():
        for filename in files:
            for secret in scan_file(filename):
                results.append(
                    {
                        "file": getattr(secret, "filename", filename),
                        "type": getattr(secret, "type", "unknown"),
                        "line_number": getattr(secret, "line_number", 0),
                    }
                )
    return results


def main(argv: list[str]) -> int:
    files = [name for name in argv if name.strip()]
    if not files:
        print(json.dumps({"valid": True, "findings": []}))
        return 0
    try:
        findings = _findings(files)
    except Exception as exc:  # fail closed: an unusable scan is not a pass
        print(json.dumps({"valid": False, "error": f"secret scan failed: {exc}"}))
        return 2
    print(json.dumps({"valid": not findings, "findings": findings}))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
