"""Announce AI Engineering OS governance at session startup (ADR-0016).

The hook only does three things: tell Codex that this project is governed,
remind it to read AGENTS.md and project fact documents, and remind it of the
governance preconditions. It never claims execution authority over Codex.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_CONTEXT = (
    "This project uses AI Engineering OS governance. Keep Codex's native "
    "engineering workflow. Before repository-changing work: read AGENTS.md and "
    "relevant docs; preview gate reasons with codex-os check (objective "
    "boundaries are enforced automatically by the Hook/Runtime: GitHub "
    "readiness, hygiene, formal-source ban without GitHub); complete "
    "open-source research when required; honor frontend approval and worktree "
    "rules; subagents submit memory candidates, the main session accepts or "
    "rejects them. At finish: run the declared test command, sync affected "
    "docs, memory, cleanup."
)


def main() -> int:
    payload = _read_payload()
    cwd = Path(str(payload.get("cwd", "."))).resolve()
    if not (cwd / ".codex-os" / "project.yaml").is_file():
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _CONTEXT,
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
