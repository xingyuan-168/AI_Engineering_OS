"""Thin stdlib-only bridge: failures must not silently permit sensitive operations."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _selector():
    root = Path(__file__).resolve().parents[1]
    source = root.parent.parent / "src/codex_ai_os/runtime_entry.py"
    path = source if source.is_file() else root / "scripts/runtime_entry.py"
    spec = importlib.util.spec_from_file_location("aios_runtime_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, root


def run_hook(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    executable = None
    code, detail = "AIOS_RUNTIME_UNAVAILABLE", "codex-os is not installed or not on PATH"
    try:
        selector, root = _selector()
        executable = selector.select_runtime(root, timeout=3)[0]
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        code, detail = getattr(exc, "code", "AIOS_RUNTIME_INCOMPATIBLE"), str(exc)
    if executable:
        try:
            result = selector.controlled_run(
                [executable, "authorize-hook"],
                input_data=json.dumps(payload),
                plugin_root=root,
                timeout=max(0.1, 10 - (time.monotonic() - started)),
            )
            if result.returncode != 0:
                code, detail = "AIOS_RUNTIME_FAILED", "authorization runtime exited unsuccessfully"
            else:
                output = json.loads(result.stdout or "null")
                if not isinstance(output, dict):
                    raise ValueError("expected an object")
                if output:
                    specific = output.get("hookSpecificOutput")
                    if set(output) != {"hookSpecificOutput"} or not isinstance(specific, dict):
                        raise ValueError("invalid hook response")
                    deny = (
                        set(specific)
                        == {"hookEventName", "permissionDecision", "permissionDecisionReason"}
                        and specific.get("hookEventName") == "PreToolUse"
                        and specific.get("permissionDecision") == "deny"
                        and isinstance(specific.get("permissionDecisionReason"), str)
                        and bool(specific["permissionDecisionReason"].strip())
                    )
                    context = (
                        set(specific) == {"hookEventName", "additionalContext"}
                        and specific.get("hookEventName") == "SessionStart"
                        and payload.get("hook_event_name") == "SessionStart"
                        and isinstance(specific.get("additionalContext"), str)
                    )
                    if not (deny or context):
                        raise ValueError("unsupported hook response")
                return output
        except subprocess.TimeoutExpired:
            code, detail = "AIOS_RUNTIME_TIMEOUT", "authorization exceeded its 10 second budget"
        except (ValueError, OSError, RuntimeError, AttributeError):
            code, detail = (
                "AIOS_RUNTIME_INVALID_RESPONSE",
                "authorization returned no valid decision",
            )
    data = payload.get("tool_input")
    data = data if isinstance(data, dict) else {}
    command = str(data.get("command", ""))
    sensitive = payload.get("tool_name") in {"apply_patch", "Write", "Edit"} or bool(
        re.search(
            r"\b(?:remove-item|rm|rmdir|rd|del|erase|set-content|add-content|out-file|"
            r"copy-item|move-item|new-item|tee|touch)\b|"
            r"\bgit\b.*\b(?:push|reset|clean|checkout|branch|update-ref)\b|[>]",
            command,
            re.I,
        )
    )
    event = str(payload.get("hook_event_name") or "PreToolUse")
    if sensitive:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": code + ": " + detail,
            }
        }
    return {
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": code + ": " + detail}
    }


def main() -> int:
    # Codex's JSON transport is UTF-8 even when Windows defaults to GBK.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("expected hook object")
    except (ValueError, OSError):
        print("AIOS_HOOK_INPUT_INVALID: unreadable hook payload", file=sys.stderr)
        return 2
    print(json.dumps(run_hook(payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
