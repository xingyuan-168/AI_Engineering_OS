from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codex_ai_os.core.gates import _copy_style_findings


def test_official_github_ssh443_alias_is_checked_in_both_gates() -> None:
    from codex_ai_os.application.repository import _remote_host as repository_host
    from codex_ai_os.core.gates import _remote_host as gate_host

    for resolver in (repository_host, gate_host):
        assert resolver("ssh://git@ssh.github.com:443/owner/repo.git") == "github.com"
        assert resolver("ssh://git@ssh.github.com:22/owner/repo.git") != "github.com"
        assert resolver("ssh://git@ssh.github.com.evil.invalid:443/owner/repo.git") != "github.com"


def test_cmake_generated_trees_need_matching_cache(tmp_path: Path) -> None:
    build = tmp_path / "build-test"
    generated = build / "CMakeFiles" / "Debug"
    generated.mkdir(parents=True)
    assert _copy_style_findings(tmp_path)
    (build / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={tmp_path}\nCMAKE_CACHEFILE_DIR:INTERNAL={build}\n",
        encoding="utf-8",
    )
    assert not _copy_style_findings(tmp_path)
    (build / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={tmp_path.parent}\nCMAKE_CACHEFILE_DIR:INTERNAL={build}\n",
        encoding="utf-8",
    )
    assert _copy_style_findings(tmp_path)


def test_generated_tree_cannot_hide_first_party_copy(tmp_path: Path) -> None:
    source = tmp_path / "src" / "real.cpp"
    source.parent.mkdir()
    source.write_bytes(b"// first party implementation\n" * 8)
    build = tmp_path / "build-test"
    generated = build / "CMakeFiles" / "Debug"
    generated.mkdir(parents=True)
    (generated / "real.cpp").write_bytes(source.read_bytes())
    (build / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={tmp_path}\nCMAKE_CACHEFILE_DIR:INTERNAL={build}\n",
        encoding="utf-8",
    )
    assert any(f.code == "FIRST_PARTY_SOURCE_COPY" for f in _copy_style_findings(tmp_path))


def test_vendor_needs_declared_origin_commit_and_clean_files(tmp_path: Path) -> None:
    vendor = tmp_path / "third_party" / "library"
    vendor.mkdir(parents=True)

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(vendor), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    git("remote", "add", "origin", "https://github.com/example/vendor.git")
    (vendor / "copy.cpp").write_text("// vendor original\n" * 8, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "test fixture")
    assert _copy_style_findings(tmp_path)
    specification = {
        "vendors": [
            {
                "path": "third_party/library",
                "origin": git("remote", "get-url", "origin"),
                "commit": git("rev-parse", "HEAD"),
            }
        ]
    }
    config = tmp_path / ".codex-os" / "source-provenance.json"
    config.parent.mkdir()
    config.write_text(json.dumps(specification), encoding="utf-8")
    assert not _copy_style_findings(tmp_path)
    (vendor / "copy.cpp").write_text("changed", encoding="utf-8")
    assert _copy_style_findings(tmp_path)
    git("checkout", "--", "copy.cpp")
    source = tmp_path / "src" / "source.cpp"
    source.parent.mkdir()
    source.write_bytes((vendor / "copy.cpp").read_bytes())
    assert any(f.code == "FIRST_PARTY_SOURCE_COPY" for f in _copy_style_findings(tmp_path))
