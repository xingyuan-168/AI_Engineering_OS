"""Add concise project governance context at session startup."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    payload = _read_payload()
    cwd = Path(str(payload.get("cwd", "."))).resolve()
    if not (cwd / ".codex-os" / "project.yaml").is_file():
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "This is an AI Engineering OS project. Use the MCP workflow state as the "
                "execution authority; repository-changing tasks require verified artifacts, "
                "one logical commit, an immediate push, and a clean worktree before completion."
            ),
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


def _read_payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
