"""Bridge between the Codex PreToolUse hook and the authorization kernel.

The hook payload is translated into kernel operations (ADR-0011):

- ``Bash`` commands become an execute request (host command screening) plus a
  write request for any redirect targets the command appears to produce.
- ``apply_patch`` payloads become a write request over every path the patch
  adds, updates, deletes or renames.

The gateway only enforces inside initialized projects (``.codex-os/
project.yaml`` present); everywhere else it stays silent so the host keeps
its default behaviour. All failures are fail-closed on the runtime side and
the hook script keeps an explicit degraded fallback for when this CLI cannot
be reached at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from codex_ai_os.application.authorization import (
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizationRequest,
    GovernanceAuthorizationKernel,
)
from codex_ai_os.application.governance_policy import GovernancePolicyCompiler

_HOOK_TOOL_OPERATIONS = {"Bash", "apply_patch"}

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
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", ""))
    cwd_value = str(payload.get("cwd", "") or ".")
    project_root = Path(cwd_value).resolve()
    if not (project_root / ".codex-os" / "project.yaml").is_file():
        return {}
    try:
        policy = GovernancePolicyCompiler(project_root).compile(("backend-project",))
    except Exception as exc:  # pragma: no cover - defensive: never fail open
        return _deny_output(
            "POLICY_COMPILE_FAILED",
            f"authorization kernel could not compile project policy: {exc}",
        )
    kernel = GovernanceAuthorizationKernel(policy)
    if tool_name == "apply_patch":
        paths = parse_apply_patch_paths(command)
        if not paths:
            return {}
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                source="hook",
                paths=paths,
            )
        )
        return _outcome_output(outcome)
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
    targets = tuple(path for path in _extract_redirect_paths(command) if path)
    if targets:
        write_outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="shell",
                source="hook",
                paths=targets,
            )
        )
        if write_outcome.decision is not AuthorizationDecision.ALLOW:
            return _outcome_output(write_outcome)
    return {}


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


__all__ = [
    "HookGatewayError",
    "authorize_hook_payload",
    "parse_apply_patch_paths",
]
