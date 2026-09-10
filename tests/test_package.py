import runpy
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
