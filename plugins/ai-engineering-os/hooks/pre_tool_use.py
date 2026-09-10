"""Codex PreToolUse governance entry point (ADR-0011 / ADR-0016).

Scope: protect user assets, not Codex's engineering execution strategy.

1. Unconditional denies target shared/irreversible assets: force push, remote
   ref deletion, direct ref surgery, persistent OCI volume destruction,
   recursive deletion of roots/home, and apply_patch writes into ``input/``.
2. Destructive-but-local operations (``git reset --hard``, ``git clean -f``,
   forced recursive deletes, ``git branch -D``) are context-aware: denied in
   the main worktree, allowed inside a disposable Worktree the agent owns or
   a system temp directory.
3. Normal engineering commands (pip/npm/pnpm/yarn/cargo installs, builds,
   ``sed -i``, tests) are never blocked: Codex stays the executor.
4. Initialized AI-OS projects additionally adjudicate apply_patch targets and
   shell redirect targets through the authorization kernel via
   ``codex-os authorize-hook`` (path policy: ``input/``/``output/`` and
   governance files).

The hook remains best-effort: a host can disable hooks, so the runtime entry
checks stay the authoritative boundary.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Unconditional denies: shared or irreversible user assets.
_UNCONDITIONAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?|(?:^|\s)-f(?:\s|$))", re.I),
        "Force push is forbidden; correct published history with a new commit or git revert.",
    ),
    (
        re.compile(r"\bgit\s+push\b[^\r\n]*(?:--delete(?:\s|$)|\s:[^\s]+)", re.I),
        "Deleting a remote ref with git push is forbidden.",
    ),
    (
        re.compile(r"\bgit\s+update-ref\b[^\r\n]*(?:^|\s)(?:-d|--delete)(?:\s|$)", re.I),
        "Deleting a Git ref directly is forbidden.",
    ),
    (
        re.compile(
            r"\b(?:docker|podman)(?:-compose|\s+compose)\b[^\r\n]*\bdown\b"
            r"[^\r\n]*(?:\s-v(?:\s|$)|--volumes?\b)",
            re.I,
        ),
        "Compose volume deletion is forbidden from the Agent path.",
    ),
    (
        re.compile(r"\b(?:docker|podman)\s+volume\s+(?:rm|prune)\b", re.I),
        "Persistent OCI volume deletion requires an independent operator workflow.",
    ),
    (
        re.compile(r"\brm\s+-[^\s]*r[^\s]*f[^\r\n]*\s(?:/|~|\$HOME)(?:\s|$)", re.I),
        "Recursive deletion of a broad root or home target is forbidden.",
    ),
)

# Context-aware denies: destructive inside the main worktree, allowed inside a
# disposable Worktree or system temp directory the agent owns.
_CONTEXT_AWARE: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+reset\s+--hard\b", re.I),
        "git reset --hard in the main worktree can discard user changes; "
        "use it only inside your own disposable Worktree.",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+--(?:\s|$)", re.I),
        "git checkout -- in the main worktree can discard user changes; "
        "use it only inside your own disposable Worktree.",
    ),
    (
        re.compile(r"\bgit\s+clean\b[^\r\n]*(?:^|\s)-[^\s]*f", re.I),
        "Forced git clean in the main worktree can delete untracked user files; "
        "use it only inside your own disposable Worktree.",
    ),
    (
        re.compile(r"\bgit\s+branch\b[^\r\n]*(?:^|\s)-D(?:\s|$)", re.I),
        "Forced local branch deletion in the main worktree is restricted; "
        "clean up task branches from your own disposable Worktree.",
    ),
    (
        re.compile(
            r"\b(?:rmdir|rd)\b(?=[^\r\n]*/s(?:\s|$))(?=[^\r\n]*/q(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "Recursive forced directory deletion is restricted to your own "
        "disposable Worktree or temp directories.",
    ),
    (
        re.compile(
            r"\b(?:del|erase)\b(?=[^\r\n]*/f(?:\s|$))(?=[^\r\n]*/s(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "Recursive forced file deletion is restricted to your own "
        "disposable Worktree or temp directories.",
    ),
    (
        re.compile(
            r"\bRemove-Item\b(?=[^\r\n]*-(?:Recurse|r)(?:\s|$))"
            r"(?=[^\r\n]*-(?:Force|fo)(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "PowerShell recursive forced deletion is restricted to your own "
        "disposable Worktree or temp directories.",
    ),
)

_COPY_STYLE_PATH = re.compile(
    r"(?:^|/)(?:src_v\d+|project_backup|backup|old|copy|final|temp|tmp|debug)(?:/|$)",
    re.I,
)
_INPUT_PATH = re.compile(r"(?:^|[\"'])input/", re.I)

_GATEWAY_TOOLS = {"Bash", "apply_patch"}


def main() -> int:
    payload = _read_payload()
    tool_name = str(payload.get("tool_name", ""))
    command = _command_of(payload)

    for pattern, reason in _UNCONDITIONAL:
        if pattern.search(command):
            print(_decision_json("deny", reason))
            return 0

    if tool_name == "apply_patch" and _targets_protected_paths(command):
        print(
            _decision_json(
                "deny",
                "input/ is read-only user input and copy-style version directories are forbidden.",
            )
        )
        return 0

    disposable = _in_disposable_area(str(payload.get("cwd", "") or "."))
    if not disposable:
        for pattern, reason in _CONTEXT_AWARE:
            if pattern.search(command):
                print(_decision_json("deny", reason))
                return 0

    if tool_name in _GATEWAY_TOOLS:
        gateway_output = _authorize_via_runtime(payload)
        if gateway_output:
            # DENY or ASK: enforce the runtime decision verbatim.
            print(gateway_output)
            return 0
        if gateway_output is not None:
            # Explicit allow from the runtime: keep the advisory context below.
            pass
        # Runtime unreachable (None): fall through to advisory-only behaviour.

    cwd = Path(str(payload.get("cwd", ".") or ".")).resolve()
    if (cwd / ".codex-os" / "project.yaml").is_file():
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": _ADVISORY_CONTEXT,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


_ADVISORY_CONTEXT = (
    "Governance boundary: protect user assets (main worktree history, input/, "
    "output/). One-off files in your own worktree or temp dirs are yours to "
    "clean. Finish with targeted tests, document impact, memory, cleanup."
)


def _in_disposable_area(cwd_value: str) -> bool:
    """True when cwd is a disposable Worktree or a system temp directory."""

    cwd = Path(cwd_value).resolve()
    posix = cwd.as_posix().casefold()
    if "/.worktrees/" in posix or posix.endswith("/.worktrees"):
        return True
    temp_roots = (
        os.environ.get("TEMP", ""),
        os.environ.get("TMP", ""),
        str(Path.home() / "AppData" / "Local" / "Temp"),
    )
    for temp in temp_roots:
        if not temp:
            continue
        try:
            cwd.relative_to(Path(temp).resolve())
        except ValueError:
            continue
        return True
    return False


def _targets_protected_paths(patch_text: str) -> bool:
    """Detect apply_patch targets inside input/ or copy-style version dirs."""

    path_pattern = re.compile(
        r"^\*\*\*\s+(?:Add|Update|Delete|Rename) File:\s*(.+?)\s*$", re.MULTILINE
    )
    for match in path_pattern.finditer(patch_text):
        normalized = match.group(1).replace("\\", "/").strip()
        if _INPUT_PATH.search(f'"{normalized}'):
            return True
        if _COPY_STYLE_PATH.search(normalized):
            return True
    return False


def _authorize_via_runtime(payload: dict[str, Any]) -> str | None:
    """Return the runtime's JSON decision, "" for allow, None when unreachable."""

    executable = shutil.which("codex-os")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "authorize-hook"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if not output:
        return ""
    try:
        decision = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(decision, dict):
        return None
    return json.dumps(decision, ensure_ascii=False)


def _command_of(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("command", ""))
    return ""


def _decision_json(decision: str, reason: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        ensure_ascii=False,
    )


def _read_payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
