"""Canonical stdlib plugin entry selector, also bundled verbatim in plugin delivery."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


class EntryError(RuntimeError):
    def __init__(self, message: str, code: str = "AIOS_RUNTIME_INCOMPATIBLE") -> None:
        super().__init__(message)
        self.code = code


def controlled_run(command, *, plugin_root: Path, timeout: float, input_data=None):
    source = plugin_root.parent.parent / "src/codex_ai_os/process_control.py"
    path = source if source.is_file() else plugin_root / "scripts/process_control.py"
    spec = importlib.util.spec_from_file_location("aios_process_control", path)
    if spec is None or spec.loader is None:
        raise EntryError("process controller is unavailable", code="AIOS_RUNTIME_CONTROL_FAILED")
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    try:
        return controller.run_owned(
            command, cwd=plugin_root, timeout=timeout, input_data=input_data
        )
    except controller.CommandStopped as exc:
        if exc.status == "timed_out":
            raise subprocess.TimeoutExpired(command, timeout) from exc
        raise EntryError(str(exc), code="AIOS_RUNTIME_CONTROL_FAILED") from exc


def select_runtime(plugin_root: Path, *, timeout: float = 3) -> list[str]:
    explicit = os.environ.get("CODEX_OS_RUNTIME")
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_absolute() or not candidate.is_file():
            raise EntryError(
                "AIOS_RUNTIME_INVALID: CODEX_OS_RUNTIME must be an absolute executable"
            )
        command = [str(candidate)]
    else:
        dev = (
            plugin_root.parent.parent
            / ".venv"
            / ("Scripts/codex-os.exe" if os.name == "nt" else "bin/codex-os")
        )
        found = str(dev) if dev.is_file() else shutil.which("codex-os")
        if not found:
            raise EntryError(
                "install codex-ai-engineering-os; aios is separate", code="AIOS_RUNTIME_UNAVAILABLE"
            )
        command = [found]
    probe = controlled_run(
        [*command, "doctor", "--runtime-only", "--json"],
        plugin_root=plugin_root,
        timeout=timeout,
    )
    try:
        identity = json.loads(probe.stdout)["data"]["runtime"]
        expected_path = plugin_root / "runtime-build.json"
        expected = (
            json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.is_file() else {}
        )
        compatible = (
            probe.returncode == 0
            and identity["product"] == "codex-ai-engineering-os"
            and identity["api_version"] == "2.0"
            and "1.2" in identity["config_versions"]
            and (not expected or identity["build_fingerprint"] == expected["build_fingerprint"])
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise EntryError(
            "AIOS_RUNTIME_INCOMPATIBLE: entry returned no compatible identity"
        ) from exc
    if not compatible:
        raise EntryError("AIOS_RUNTIME_INCOMPATIBLE: runtime and plugin product/build do not match")
    return command


def main() -> int:
    started = time.monotonic()
    plugin_root = Path(os.environ.get("PLUGIN_ROOT") or Path(sys.argv[0]).resolve().parent.parent)
    try:
        command = select_runtime(plugin_root)
        return subprocess.call([*command, *sys.argv[1:]])
    except (EntryError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc) + f" ({time.monotonic() - started:.2f}s)", file=sys.stderr)
        return 50


if __name__ == "__main__":
    raise SystemExit(main())
