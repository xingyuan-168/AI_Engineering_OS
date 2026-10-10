import hashlib
import json
import re
import runpy
import tomllib
import zipfile
from pathlib import Path

import pytest

from codex_ai_os import RUNTIME_VERSIONS, __version__
from codex_ai_os.cli import app as cli_app


def test_package_exposes_version() -> None:
    assert __version__ == "1.0.0"


def test_runtime_version_matrix_is_single_release_truth() -> None:
    assert RUNTIME_VERSIONS.software == __version__
    assert RUNTIME_VERSIONS.plugin == "1.0.0"
    assert RUNTIME_VERSIONS.api == "2.0"
    assert RUNTIME_VERSIONS.config_schema == "1.2"
    assert RUNTIME_VERSIONS.document_schema == "1.2"
    assert RUNTIME_VERSIONS.sqlite_schema == "0001"
    assert RUNTIME_VERSIONS.requirement_baseline == "REQ-GC-1.0"
    assert RUNTIME_VERSIONS.git_tag == "v1.0.0"
    assert RUNTIME_VERSIONS.as_dict()["software"] == "1.0.0"


def test_package_module_entrypoint_dispatches_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cli_app, "app", lambda: called.append(True))
    entrypoint = Path(cli_app.__file__).parents[1] / "__main__.py"

    runpy.run_path(str(entrypoint), run_name="codex_ai_os.not_main")
    runpy.run_path(str(entrypoint), run_name="__main__")

    assert called == [True]


def test_sdist_keeps_script_contracts_and_windows_launcher_exit_status() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "scripts" in metadata["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    launcher = (root / "plugins/ai-engineering-os/scripts/launch_mcp.cmd").read_text()
    assert "if %ERRORLEVEL% EQU 0 (" not in launcher
    assert len(re.findall(r"mcp\nexit /b %ERRORLEVEL%", launcher)) == 1
    assert "runtime_entry.py" in launcher
    assert (root / "plugins/ai-engineering-os/scripts/runtime_entry.py").read_bytes() == (
        root / "src/codex_ai_os/runtime_entry.py"
    ).read_bytes()


def test_wheel_plugin_delivery_share_exact_runtime_and_skill_contracts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    build = runpy.run_path(str(root / "scripts/build_delivery.py"))["build"]
    output = build(root, tmp_path)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["installation"] == "not_performed"
    wheel = next(output.glob("*.whl"))
    with (
        zipfile.ZipFile(wheel) as runtime,
        zipfile.ZipFile(output / "ai-engineering-os.zip") as plugin,
    ):
        assert runtime.read("codex_ai_os/runtime_entry.py") == plugin.read(
            "scripts/runtime_entry.py"
        )
        assert runtime.read("codex_ai_os/process_control.py") == plugin.read(
            "scripts/process_control.py"
        )
        assert json.loads(plugin.read("runtime-build.json")) == manifest["runtime"]
        assert json.loads(plugin.read(".codex-plugin/plugin.json"))["version"] == (
            manifest["plugin_version"]
        )
        assert manifest["plugin_version"].endswith(manifest["delivery_fingerprint"][:12])
        assert all(
            hashlib.sha256(plugin.read(name)).hexdigest() == digest
            for name, digest in manifest["plugin_files"].items()
        )
        skills = [name for name in plugin.namelist() if name.endswith("/SKILL.md")]
        assert len(skills) == 8
        for name in skills:
            assert plugin.read(name) == (root / "plugins/ai-engineering-os" / name).read_bytes()
        assert b"CODEX_OS_RUNTIME" in plugin.read(".mcp.json")
    with pytest.raises(FileExistsError):
        build(root, tmp_path)
