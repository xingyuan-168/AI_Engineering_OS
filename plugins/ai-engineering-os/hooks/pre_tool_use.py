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
   shell redirect targets through the authorization kernel via the
   codex-os authorize-hook CLI bridge (path policy: input/ / output/ and
   governance files); this kernel screening runs in the main worktree only.
5. Memory single-writer rule: disposable worktrees never write the
   docs/memory/ source of truth; subagents submit candidates instead.

The hook remains best-effort: a host can disable hooks, so the runtime entry
checks stay the authoritative boundary.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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
_MEMORY_JSONL_PATH = re.compile(r"docs/memory/", re.I)

_GATEWAY_TOOLS = {"Bash", "apply_patch", "Write", "Edit"}


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

    # Memory single-writer rule (ADR-0016): subagents inside disposable
    # worktrees never write docs/memory/; they submit candidates instead.
    if disposable and tool_name in _GATEWAY_TOOLS:
        memory_attempt = (
            _memory_writer_command(command)
            if tool_name == "Bash"
            else _targets_memory_paths(command)
        )
        if memory_attempt:
            print(
                _decision_json(
                    "deny",
                    "Memory single-writer rule: a disposable worktree must not write "
                    "docs/memory/; submit a candidate (codex-os memory record "
                    "--candidate) and let the main session merge it at finish.",
                )
            )
            return 0

    if tool_name in _GATEWAY_TOOLS and not disposable:
        # Code Start write boundary, degraded fallback (ADR-0016): when the
        # runtime CLI is unreachable, a formal source write without a GitHub
        # remote is still denied instead of only being reminded.
        offline = _offline_formal_write_denied(payload)
        if offline is not None:
            print(offline)
            return 0
        # Kernel screening covers main-worktree operations only; disposable
        # worktrees keep the context-aware allowance above (ADR-0016).
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
    """True only for a trusted disposable Worktree or a system temp directory.

    A trusted worktree must satisfy all of (ADR-0016):
    1. the coordinator project root can be resolved;
    2. a registration exists in the runtime database;
    3. the registered path matches the current cwd realpath;
    4. the worktree really exists in git worktree list --porcelain.
    Any fake ".worktrees/" path fails closed. System temp detection uses
    tempfile.gettempdir() (Windows/Linux/macOS) plus TEMP/TMP supplements.
    """

    cwd = Path(cwd_value).resolve()
    if _trusted_disposable_worktree(cwd):
        return True
    for temp in _temp_roots():
        try:
            cwd.relative_to(temp)
        except ValueError:
            continue
        return True
    return False


def _temp_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    candidates = [tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", "")]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = Path(candidate).resolve()
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _trusted_disposable_worktree(cwd: Path) -> bool:
    registered = _registered_worktree_row(cwd)
    if registered is None:
        return False
    record_path = registered[1]
    coordinator = _worktree_coordinator_root(cwd)
    if coordinator is None:
        return False
    absolute = (coordinator / record_path).resolve()
    try:
        Path(os.path.realpath(cwd)).relative_to(Path(os.path.realpath(absolute)))
    except ValueError:
        return False
    listing = subprocess.run(
        ["git", "-C", str(coordinator), "worktree", "list", "--porcelain"],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if listing.returncode != 0:
        return False
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            listed = line[len("worktree "):].strip()
            if os.path.normcase(listed) == os.path.normcase(str(absolute)):
                return True
    return False


def _worktree_coordinator_root(cwd: Path) -> Path | None:
    """Resolve the coordinator root from a linked worktree cwd (git file)."""

    marker = cwd
    for current in [cwd, *cwd.parents]:
        git_entry = current / ".git"
        if git_entry.is_file():
            marker = current
            break
    else:
        return None
    try:
        first_line = (marker / ".git").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeError, IndexError):
        return None
    if not first_line.casefold().startswith("gitdir:"):
        return None
    raw = first_line[len("gitdir:"):].strip()
    git_dir = Path(raw) if Path(raw).is_absolute() else (marker / raw)
    common_file = git_dir / "commondir"
    try:
        raw_common = (common_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    common = Path(raw_common) if Path(raw_common).is_absolute() else (git_dir / raw_common)
    try:
        common = common.resolve()
    except OSError:
        return None
    return common.parent if common.name == ".git" else None


def _registered_worktree_row(cwd: Path) -> tuple[str, str] | None:
    """Return (name, record_path) when cwd matches one registration exactly."""

    coordinator = _worktree_coordinator_root(cwd)
    if coordinator is None:
        return None
    database_path = coordinator / ".codex-os" / "state" / "state.db"
    if not database_path.is_file():
        return None
    try:
        connection = sqlite3.connect(str(database_path), timeout=2)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            "SELECT name, path FROM worktrees WHERE disposable = 1"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    for name, path in rows:
        record_path = str(path).replace(chr(92), "/").rstrip("/")
        absolute = coordinator / record_path
        try:
            Path(os.path.realpath(cwd)).relative_to(Path(os.path.realpath(absolute)))
        except ValueError:
            continue
        return str(name), record_path
    return None


def _targets_memory_paths(patch_text: str) -> bool:
    """Detect apply_patch targets under the memory source of truth."""

    path_pattern = re.compile(
        r"^\*\*\*\s+(?:Add|Update|Delete|Rename) File:\s*(.+?)\s*$", re.MULTILINE
    )
    for match in path_pattern.finditer(patch_text):
        normalized = match.group(1).replace("\\", "/").strip()
        if _MEMORY_JSONL_PATH.search(normalized):
            return True
    return False


def _memory_writer_command(command: str) -> bool:
    """Detect shell-level writes into the memory source of truth."""

    if re.search(r"\bcodex-os\s+memory\s+record\b", command, re.I) and not re.search(
        r"--candidate\b", command, re.I
    ):
        return True
    return bool(re.search(r">\s*[\"']?[^\r\n;|&]*docs/memory/", command, re.I))


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


_APPLY_PATCH_TARGET = re.compile(
    r"^\*\*\*\s+(?:Add|Update|Delete|Rename) File:\s*(.+?)\s*$", re.MULTILINE
)
_REDIRECT_TARGET = re.compile(r"(?<![-<>])>{1,2}\s*([^\s|;&<>]+)", re.MULTILINE)
_CODE_PATHS_BLOCK = re.compile(r"(?m)^code_paths:\s*$\n((?:^[ \t]+-.*$\n?)+)")
_CODE_PATH_ENTRY = re.compile(r"(?m)^[ \t]+-\s*(\S+)\s*$")
_IGNORED_REDIRECTS = {"&1", "&2", "/dev/null", "nul", "$null", "null"}


def _write_target_paths(payload: dict[str, Any]) -> list[str]:
    """Best-effort write targets for one payload (offline fallback)."""

    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input")
    input_dict = tool_input if isinstance(tool_input, dict) else {}
    command = str(input_dict.get("command", ""))
    paths: list[str] = []
    if tool_name == "apply_patch":
        paths.extend(match.strip() for match in _APPLY_PATCH_TARGET.findall(command))
    elif tool_name in {"Write", "Edit"}:
        raw = input_dict.get("path") or input_dict.get("file_path") or ""
        paths.append(str(raw).replace(chr(92), "/").strip())
    elif tool_name == "Bash":
        for match in _REDIRECT_TARGET.finditer(command):
            raw = match.group(1).strip().strip(chr(34)).strip(chr(39))
            if raw.casefold() not in _IGNORED_REDIRECTS:
                paths.append(raw.replace(chr(92), "/"))
    return [path.lstrip("./") for path in paths if path.strip()]


def _code_paths(project_root: Path) -> tuple[str, ...]:
    """Read code_paths from project.yaml text; default to a src/ only tree."""

    marker = project_root / ".codex-os" / "project.yaml"
    try:
        text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ("src",)
    block = _CODE_PATHS_BLOCK.search(text)
    if block is None:
        return ("src",)
    entries = _CODE_PATH_ENTRY.findall(block.group(1))
    return tuple(entries) if entries else ("src",)


def _offline_formal_write_denied(payload: dict[str, Any]) -> str | None:
    """Offline Code Start boundary: deny formal source writes when the
    runtime CLI is unreachable and the project has no usable GitHub remote.
    None means the case does not apply."""

    if shutil.which("codex-os") is not None:
        return None  # Runtime reachable: the gateway adjudicates instead.
    cwd = Path(str(payload.get("cwd", ".") or ".")).resolve()
    if not (cwd / ".codex-os" / "project.yaml").is_file():
        return None
    targets = _write_target_paths(payload)
    if not targets:
        return None
    formal: list[str] = []
    for target in targets:
        for base in _code_paths(cwd):
            base = base.rstrip("/")
            if target == base or target.startswith(base + "/"):
                formal.append(target)
                break
    if not formal:
        return None
    probe = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if probe.returncode == 0:
        return None
    return _decision_json(
        "deny",
        "CODE_START_BLOCKED: no GitHub remote is configured, so formal source "
        "writes (" + ", ".join(formal) + ") are blocked; research, analysis, "
        "and documentation stay allowed.",
    )


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
