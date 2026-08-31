"""Block obvious destructive host commands as a defense-in-depth guard."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_FORBIDDEN: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?|(?:^|\s)-f(?:\s|$))", re.I),
        "Force push is forbidden; correct published history with a new commit or git revert.",
    ),
    (
        re.compile(r"\bgit\s+reset\s+--hard\b", re.I),
        "git reset --hard is forbidden because it can discard user changes.",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+--(?:\s|$)", re.I),
        "git checkout -- is forbidden because it can discard user changes.",
    ),
    (
        re.compile(r"\bgit\s+clean\b[^\r\n]*(?:^|\s)-[^\s]*f", re.I),
        "Forced git clean is forbidden because it can delete untracked user files.",
    ),
    (
        re.compile(r"\brm\s+-[^\s]*r[^\s]*f[^\r\n]*\s(?:/|~|\$HOME)(?:\s|$)", re.I),
        "Recursive deletion of a broad root or home target is forbidden.",
    ),
    (
        re.compile(
            r"\b(?:rmdir|rd)\b(?=[^\r\n]*/s(?:\s|$))(?=[^\r\n]*/q(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "Recursive forced directory deletion is forbidden on the Windows host.",
    ),
    (
        re.compile(
            r"\b(?:del|erase)\b(?=[^\r\n]*/f(?:\s|$))(?=[^\r\n]*/s(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "Recursive forced file deletion is forbidden on the Windows host.",
    ),
    (
        re.compile(
            r"\bRemove-Item\b(?=[^\r\n]*-(?:Recurse|r)(?:\s|$))"
            r"(?=[^\r\n]*-(?:Force|fo)(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "PowerShell recursive forced deletion is forbidden on the Windows host.",
    ),
    (
        re.compile(r"\bgit\s+push\b[^\r\n]*(?:--delete(?:\s|$)|\s:[^\s]+)", re.I),
        "Deleting a remote ref with git push is forbidden.",
    ),
    (
        re.compile(r"\bgit\s+branch\b[^\r\n]*(?:^|\s)-D(?:\s|$)", re.I),
        "Forced local branch deletion is forbidden.",
    ),
    (
        re.compile(r"\bgit\s+update-ref\b[^\r\n]*(?:^|\s)(?:-d|--delete)(?:\s|$)", re.I),
        "Deleting a Git ref directly is forbidden.",
    ),
)


def main() -> int:
    payload = _read_payload()
    tool_input = payload.get("tool_input")
    command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
    for pattern, reason in _FORBIDDEN:
        if pattern.search(command):
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        }
                    },
                    ensure_ascii=False,
                )
            )
            return 0

    cwd = Path(str(payload.get("cwd", "."))).resolve()
    if (cwd / ".codex-os" / "project.yaml").is_file():
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": (
                            "Keep this change inside the active task's allowed paths and attach "
                            "commit, push, artifact-hash, and verification evidence before "
                            "completion."
                        ),
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


def _read_payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
