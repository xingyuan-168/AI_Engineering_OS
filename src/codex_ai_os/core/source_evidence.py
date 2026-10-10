"""Narrow local provenance for hygiene candidates; never a directory-name waiver."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.adapters.process import ExecutionStopped
from codex_ai_os.core.github_remote import remote_host


def _require(result, label: str, allowed=(0,)):
    if result.returncode not in allowed:
        raise OSError("Git provenance query failed: " + label)
    return result


_PROBE = re.compile(
    r"CMakeFiles/[0-9][^/]+/(?:CompilerId(?:C|CXX)/(?:Debug|tmp)|"
    r"(?:VCTargetsPath/)?(?:(?:x64|x86|Win32|ARM64)/)?Debug)$",
    re.I,
)
_GENERATED_EXTENSIONS = {
    ".obj",
    ".o",
    ".log",
    ".tlog",
    ".exe",
    ".dll",
    ".pdb",
    ".idb",
    ".ilk",
    ".lastbuildstate",
    ".recipe",
    ".manifest",
    ".txt",
    ".bin",
}


def repository_identity(url: str) -> str | None:
    if remote_host(url) != "github.com":
        return None
    path = url.split(":", 1)[1] if url.startswith("git@") else urlsplit(url).path
    return "github.com/" + path.strip("/").removesuffix(".git").casefold()


class SourceEvidence:
    def __init__(self, root: Path, git: GitRunner, tracked: set[str]) -> None:
        self.root, self.git, self.tracked = root.resolve(), git, tracked
        self.dependencies = {}
        self.parent_sources = None
        self.source_hashes = None

    def first_party_copy(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        if not any(
            p.casefold() in {"build", "vendor", "third_party"} or p.casefold().startswith("build-")
            for p in relative.parts[:-1]
        ):
            return False
        if path.suffix.casefold() not in {
            ".py",
            ".cpp",
            ".c",
            ".hpp",
            ".h",
            ".cu",
            ".js",
            ".ts",
            ".rs",
        }:
            return False
        if self.source_hashes is None:
            from codex_ai_os.infrastructure.config import load_project_config, resolve_runtime_root

            try:
                prefixes = load_project_config(
                    resolve_runtime_root(self.root).project_root
                ).code_paths
            except ValueError as exc:
                if isinstance(exc, ExecutionStopped):
                    raise
                prefixes = ("src",)
            self.source_hashes = set()
            for name in self.tracked:
                if any(name == prefix or name.startswith(prefix + "/") for prefix in prefixes):
                    source = self.root / name
                    if source.is_file() and not source.is_symlink():
                        self.source_hashes.add(
                            hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).digest()
                        )
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest()
        return digest in self.source_hashes and self._upstream(path) is None

    def classify(self, path: Path) -> dict | None:
        if path.is_symlink() or path.is_junction():
            return None
        generated = self._cmake(path)
        if generated:
            return generated
        return self._upstream(path)

    def _cmake(self, path: Path) -> dict | None:
        if not path.is_dir():
            return None
        relative = path.relative_to(self.root).as_posix()
        if any(p == relative or p.startswith(relative + "/") for p in self.tracked):
            return None
        for build in path.parents:
            if build == self.root.parent:
                break
            cache = build / "CMakeCache.txt"
            if not cache.is_file():
                continue
            local = path.relative_to(build).as_posix()
            if not _PROBE.fullmatch(local):
                return None
            values = {}
            for line in cache.read_text(encoding="utf-8").splitlines():
                if "=" in line and ":" in line.split("=", 1)[0]:
                    key, value = line.split("=", 1)
                    values[key.split(":", 1)[0]] = value
            source = values.get("CMAKE_HOME_DIRECTORY")
            directory = values.get("CMAKE_CACHEFILE_DIR")
            if (
                not source
                or not directory
                or Path(source).resolve() != self.root
                or Path(directory).resolve() != build
            ):
                return None
            if self.git.run("check-ignore", "--quiet", "--", relative).returncode:
                return None
            for current, dirs, files in os.walk(path, followlinks=False):
                for name in dirs:
                    child = Path(current) / name
                    if child.is_symlink() or child.is_junction() or not name.endswith(".tlog"):
                        return None
                for name in files:
                    child = Path(current) / name
                    if child.is_symlink() or child.suffix.casefold() not in _GENERATED_EXTENSIONS:
                        return None
            return {
                "category": "generated_cmake_compiler_probe",
                "cache": str(cache),
                "source_root": str(self.root),
                "build_root": str(build),
                "git": "ignored_untracked",
            }
        return None

    def _dependency(self, root: Path):
        if root in self.dependencies:
            return self.dependencies[root]
        git = GitRunner(root)
        result = None
        try:
            origin = git.run("remote", "get-url", "origin")
            head = git.run("rev-parse", "--verify", "HEAD")
            _require(origin, "dependency origin", (0, 2))
            _require(head, "dependency HEAD")
            identity = (
                repository_identity(origin.stdout.strip()) if origin.returncode == 0 else None
            )
            sha = head.stdout.strip()
            if self.parent_sources is None:
                names = self.git.run("remote")
                _require(names, "parent remotes")
                self.parent_sources = set()
                for name in names.stdout.splitlines():
                    urls = self.git.run("remote", "get-url", "--all", name)
                    _require(urls, "parent URLs")
                    self.parent_sources.update(
                        repository_identity(u) for u in urls.stdout.splitlines()
                    )
            if (
                not identity
                or identity in self.parent_sources
                or not re.fullmatch(r"[a-f0-9]{40}", sha)
            ):
                self.dependencies[root] = None
                return None
            relative = root.relative_to(self.root).as_posix()
            registered = self.git.run("ls-files", "--stage", "-z", "--", ":(literal)" + relative)
            _require(registered, "submodule registration")
            submodule = any(
                row.startswith("160000 " + sha + " ") and row.split("\t", 1)[-1] == relative
                for row in registered.stdout.split("\0")
                if row
            )
            if submodule:
                result = (git, sha, origin.stdout.strip(), "submodule")
            else:
                gitdir = git.run("rev-parse", "--absolute-git-dir")
                _require(gitdir, "dependency git directory")
                fetched = Path(gitdir.stdout.strip()) / "FETCH_HEAD"
                for line in fetched.read_text(encoding="utf-8").splitlines():
                    fields = line.split("\t")
                    if fields[0] == sha and " of " in fields[-1]:
                        source = fields[-1].rsplit(" of ", 1)[1]
                        if repository_identity(source) == identity:
                            result = (git, sha, origin.stdout.strip(), "fetch_head")
                            break
        except (FileNotFoundError, ValueError) as exc:
            if isinstance(exc, ExecutionStopped):
                raise
            result = None
        self.dependencies[root] = result
        return result

    def _upstream(self, path: Path) -> dict | None:
        for parent in (path if path.is_dir() else path.parent, *path.parents):
            if parent == self.root:
                break
            if not (parent / ".git").exists():
                continue
            dependency = self._dependency(parent)
            if not dependency:
                return None
            git, sha, origin, proof = dependency
            relative = path.relative_to(parent).as_posix()
            spec = ":(literal)" + relative
            tree = git.run("ls-tree", "-r", "-z", sha, "--", spec)
            _require(tree, "pinned file tree")
            if tree.returncode or not tree.stdout:
                return None
            if not self._matches_tree(parent, path, git, tree.stdout):
                return None
            return {
                "category": "pinned_upstream_source",
                "dependency_root": str(parent),
                "source": origin,
                "revision": sha,
                "proof": proof,
            }
        return None

    @staticmethod
    def _matches_tree(parent: Path, path: Path, git: GitRunner, tree: str) -> bool:
        """Hash real files without writing objects or trusting index stat/skip flags."""
        expected = {}
        expected_dirs = set()
        for row in tree.split("\0"):
            if not row:
                continue
            metadata, name = row.split("\t", 1)
            mode, kind, digest = metadata.split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                return False
            expected[name] = digest
            expected_dirs.update(p.as_posix() for p in Path(name).parents)

        def unreadable(error):
            raise error

        actual = set()
        if path.is_dir():
            for current, dirs, files in os.walk(path, followlinks=False, onerror=unreadable):
                for name in dirs:
                    child = Path(current) / name
                    if (
                        child.is_symlink()
                        or child.is_junction()
                        or child.relative_to(parent).as_posix() not in expected_dirs
                    ):
                        return False
                actual.update(
                    (Path(current) / name).relative_to(parent).as_posix() for name in files
                )
        else:
            actual.add(path.relative_to(parent).as_posix())
        if actual != set(expected):
            return False
        for name in actual:
            child = parent / name
            if not child.is_file() or child.is_symlink() or child.is_junction():
                return False
        names = sorted(actual)
        for offset in range(0, len(names), 16):
            batch = names[offset : offset + 16]
            attributes = git.run("check-attr", "-z", "filter", "--", *batch)
            _require(attributes, "upstream content attributes")
            fields = attributes.stdout.split("\0")
            if len(fields) != 3 * len(batch) + 1:
                raise OSError("Git provenance attribute result was incomplete")
            if any(value not in {"unspecified", "unset"} for value in fields[2::3]):
                raise OSError("upstream content needs an external filter; provenance is unverified")
            hashes = git.run("hash-object", "--", *batch)
            _require(hashes, "actual upstream file content")
            values = hashes.stdout.splitlines()
            if values != [expected[name] for name in batch]:
                return False
        return True
