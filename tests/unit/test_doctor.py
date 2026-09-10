from __future__ import annotations

import json
from pathlib import Path

from codex_ai_os.application.doctor import DoctorService


def test_doctor_core_environment_passes_without_plugin(tmp_path: Path) -> None:
    report = DoctorService(tmp_path).run()
    names = [check.name for check in report.checks]
    assert {"python", "git", "codex", "sqlite-fts5", "path-encoding", "plugin-hooks"} == set(names)
    for name in ("python", "git", "sqlite-fts5", "path-encoding"):
        check = next(item for item in report.checks if item.name == name)
        assert check.ok is True, (name, check.detail)
    hooks = next(item for item in report.checks if item.name == "plugin-hooks")
    assert hooks.required is False
    assert hooks.ok is True
    assert report.ok is True
    assert report.path_encoding_corrupt is False


def test_doctor_reports_missing_hook_scripts(tmp_path: Path) -> None:
    manifest = tmp_path / "plugins" / "ai-engineering-os" / "hooks"
    manifest.mkdir(parents=True)
    (manifest / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "commandWindows": 'py -3 "MISSING_HOOK.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    report = DoctorService(tmp_path).run()
    hooks = next(item for item in report.checks if item.name == "plugin-hooks")
    assert hooks.ok is False
    assert "MISSING_HOOK.py" in hooks.detail
