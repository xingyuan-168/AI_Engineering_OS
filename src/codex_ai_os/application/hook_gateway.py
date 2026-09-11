"""Bridge between the Codex PreToolUse hook and the authorization kernel.

The hook payload is translated into kernel operations (ADR-0011) plus the
enforced Code Start write boundary (ADR-0016):

- "Bash" commands become an execute request (host command screening) plus a
  write request for any redirect targets the command appears to produce.
- "apply_patch" payloads become a write request over every path the patch
  adds, updates, deletes or renames.
- "Write"/"Edit" payloads become a write request over their target path.

When a write targets the formal source tree (the project "code_paths"), the
stateless Code Start boundary check runs first: a blocking finding becomes
a real deny, not a reminder. Declared write targets that collide with the
user's own uncommitted changes ask for confirmation instead of silently
overwriting them.

The gateway only enforces inside initialized projects (".codex-os/
project.yaml" present; registered disposable worktrees map to their
coordinator project through the shared root resolution); everywhere else it
stays silent so the host keeps its default behaviour. All failures are
fail-closed on the runtime side and the hook script keeps an explicit
degraded fallback for when this CLI cannot be reached at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application.authorization import (
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizationRequest,
    GovernanceAuthorizationKernel,
)
from codex_ai_os.application.governance_policy import GovernancePolicyCompiler
from codex_ai_os.core.gates import GateDecision, formal_write_blockers
from codex_ai_os.infrastructure.config import (
    ProjectRootError,
    load_project_config,
    resolve_runtime_root,
)

_HOOK_TOOL_OPERATIONS = {"Bash", "apply_patch", "Write", "Edit"}

_ADD_FILE = re.compile(r"^\*\*\*\s+Add File:\s*(.+?)\s*$", re.MULTILINE)
_UPDATE_FILE = re.compile(r"^\*\*\*\s+Update File:\s*(.+?)\s*$", re.MULTILINE)
_DELETE_FILE = re.compile(r"^\*\*\*\s+Delete File:\s*(.+?)\s*$", re.MULTILINE)
_RENAME_FILE = re.compile(r"^\*\*\*\s+Rename File:\s*(.+?)\s*$", re.MULTILINE)
_MOVE_TO = re.compile(r"^\*\*\*\s+Move to:\s*(.+?)\s*$", re.MULTILINE)
_REDIRECT_TARGETS = re.compile(r"(?<![-<>])>{1,2}\s*([^\s|;&<>]+)", re.MULTILINE)


class HookGatewayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def authorize_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the Codex hook JSON for one PreToolUse payload (may be empty)."""

    tool_name = str(payload.get("tool_name", ""))
    if tool_name not in _HOOK_TOOL_OPERATIONS:
        return {}
    tool_input = payload.get("tool_input")
    input_dict: dict[str, Any] = tool_input if isinstance(tool_input, dict) else {}
    command = str(input_dict.get("command", ""))
    cwd_value = str(payload.get("cwd", "") or ".")
    try:
        resolved = resolve_runtime_root(Path(cwd_value))
    except ProjectRootError as exc:
        # Unregistered .worktrees paths never gain disposable-worktree rights.
        return _deny_output(exc.code, str(exc))
    project_root = resolved.project_root
    if not (project_root / ".codex-os" / "project.yaml").is_file():
        return {}
    try:
        config = load_project_config(project_root)
        policy = GovernancePolicyCompiler(project_root).compile()
    except Exception as exc:  # pragma: no cover - defensive: never fail open
        return _deny_output(
            "POLICY_COMPILE_FAILED",
            f"authorization kernel could not compile project policy: {exc}",
        )
    kernel = GovernanceAuthorizationKernel(policy)

    if tool_name == "Bash":
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="execute",
                tool="shell",
                source="hook",
                command=command,
            )
        )
        if outcome.decision is AuthorizationDecision.DENY:
            return _outcome_output(outcome)

    write_targets = _write_targets(tool_name, command, input_dict)
    if not write_targets:
        return {}
    formal = _formal_write_targets(write_targets, config.code_paths)
    if formal:
        boundary = _formal_boundary(project_root, config.github_hosts)
        if not boundary.allowed:
            codes = ", ".join(
                finding.code for finding in boundary.findings if finding.blocking
            )
            return _deny_output(
                "CODE_START_BLOCKED",
                "formal source write is blocked by the Code Start gate ("
                + codes
                + "); resolve the findings or work outside "
                + ", ".join(config.code_paths),
            )
        conflicts = _user_dirty_conflicts(project_root, formal)
        if conflicts:
            return _ask_output(
                "USER_DIRTY_CONFLICT",
                "the user has uncommitted changes in "
                + ", ".join(conflicts)
                + "; isolate the task in a registered worktree or request "
                "explicit confirmation before overwriting",
            )
    outcome = kernel.authorize(
        AuthorizationRequest(
            principal="backend-engineer",
            operation="write",
            tool=tool_name,
            source="hook",
            paths=write_targets,
        )
    )
    return _outcome_output(outcome)


def parse_apply_patch_paths(patch_text: str) -> tuple[str, ...]:
    """Extract every target path referenced by an apply_patch payload."""

    paths: list[str] = []
    for pattern in (_ADD_FILE, _UPDATE_FILE, _DELETE_FILE, _RENAME_FILE):
        paths.extend(match.strip() for match in pattern.findall(patch_text))
    for rename_block in _RENAME_FILE.split(patch_text)[1:]:
        move = _MOVE_TO.search(rename_block)
        if move is not None:
            paths.append(move.group(1).strip())
    ordered: list[str] = []
    for path in paths:
        normalized = path.replace("\\", "/").strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return tuple(ordered)


def _formal_boundary(
    project_root: Path, github_hosts: frozenset[str] | tuple[str, ...]
) -> GateDecision:
    """Indirection point for the Code Start boundary (tests monkeypatch this)."""

    return formal_write_blockers(project_root, github_hosts=github_hosts)


def _write_targets(
    tool_name: str, command: str, tool_input: dict[str, Any]
) -> tuple[str, ...]:
    """Collect the paths one hook payload would write."""

    if tool_name == "apply_patch":
        return parse_apply_patch_paths(command)
    if tool_name in {"Write", "Edit"}:
        raw = tool_input.get("path") or tool_input.get("file_path") or ""
        normalized = str(raw).replace("\\", "/").strip()
        return (normalized,) if normalized else ()
    return tuple(path for path in _extract_redirect_paths(command) if path)


def _formal_write_targets(
    targets: tuple[str, ...], code_paths: tuple[str, ...] | list[str]
) -> tuple[str, ...]:
    """Return the targets that fall under the project's formal code paths."""

    formal: list[str] = []
    for target in targets:
        posix = target.strip().lstrip("./").replace("\\", "/")
        for code_path in code_paths:
            base = code_path.strip().rstrip("/")
            if posix == base or posix.startswith(base + "/"):
                formal.append(posix)
                break
    return tuple(dict.fromkeys(formal))


def _user_dirty_conflicts(
    project_root: Path, formal: tuple[str, ...]
) -> tuple[str, ...]:
    """Return formal targets the user already has uncommitted changes in."""

    status = GitRunner(project_root).run("status", "--porcelain")
    if status.returncode != 0:
        return ()
    dirty: set[str] = set()
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        dirty.add(entry.strip().strip('"').replace("\\", "/"))
    wanted = {path.casefold() for path in formal}
    return tuple(sorted(path for path in dirty if path.casefold() in wanted))


def _extract_redirect_paths(command: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _REDIRECT_TARGETS.finditer(command):
        raw = match.group(1).strip().strip("'\"")
        if not raw or raw.casefold() in {"&1", "&2", "/dev/null", "nul", "$null", "null"}:
            continue
        paths.append(raw)
    return tuple(paths)


def _outcome_output(outcome: AuthorizationOutcome) -> dict[str, Any]:
    if outcome.decision is AuthorizationDecision.ALLOW:
        return {}
    details = outcome.reason
    if outcome.denied_paths:
        details = f"{details}: {sorted(outcome.denied_paths)}"
    if outcome.ask_paths:
        details = f"{details}: {sorted(outcome.ask_paths)}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": outcome.decision.value,
            "permissionDecisionReason": f"{outcome.rule_id}: {details}",
        }
    }


def _deny_output(rule_id: str, reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{rule_id}: {reason}",
        }
    }


def _ask_output(rule_id: str, reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"{rule_id}: {reason}",
        }
    }


__all__ = [
    "HookGatewayError",
    "authorize_hook_payload",
    "parse_apply_patch_paths",
]
