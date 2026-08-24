from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.secret_scan import SecretScanError, main, scan_repository


def test_secret_scan_reports_only_metadata_and_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repository(tmp_path / "secret-repository")
    token = "sk-" + "a" * 48
    (root / "credentials.py").write_text(f'api_key = "{token}"\n', encoding="utf-8")
    _git(root, "add", "credentials.py")
    _git(root, "commit", "-m", "test: add scanner fixture")

    result = scan_repository(root)

    assert result["valid"] is False
    assert result["finding_count"] == 1
    assert result["findings"] == [
        {"filename": "credentials.py", "line_number": 1, "type": "Secret Keyword"}
    ]
    assert token not in json.dumps(result)
    assert main([str(root)]) == 1
    assert token not in capsys.readouterr().out


def test_secret_scan_accepts_allowlisted_tracked_repository(tmp_path: Path) -> None:
    root = _repository(tmp_path / "clean-repository")
    (root / "settings.py").write_text(
        'api_key = "fixture"  # pragma: allowlist secret\n', encoding="utf-8"
    )
    _git(root, "add", "settings.py")
    _git(root, "commit", "-m", "test: add clean scanner fixture")

    result = scan_repository(root)

    assert result == {
        "valid": True,
        "tracked_files": 1,
        "finding_count": 0,
        "findings": [],
    }


def test_secret_scan_rejects_missing_or_empty_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(SecretScanError, match=r"cannot|not a git repository"):
        scan_repository(missing)
    assert main([str(missing)]) == 2
    assert "secret scan failed" in capsys.readouterr().err

    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-b", "main")
    with pytest.raises(SecretScanError, match="no Git-tracked files"):
        scan_repository(empty)


def _repository(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Secret Scanner")
    _git(root, "config", "user.email", "scanner@example.invalid")
    return root


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
