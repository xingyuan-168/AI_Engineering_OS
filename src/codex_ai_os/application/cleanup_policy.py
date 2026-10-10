"""Read-only checks for literal cleanup targets, not an execution/ownership service."""

from __future__ import annotations

import configparser
import os
import re
import shlex
import stat
import tempfile
import time
from pathlib import Path

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.infrastructure.config import locate_checkout

DELETE_COMMAND = re.compile(r"\b(?:remove-item|rm|rmdir|rd|del|erase)\b", re.I)
_FLAGS = frozenset(
    {
        "-literalpath",
        "-path",
        "-recurse",
        "-force",
        "-r",
        "-f",
        "-rf",
        "-fr",
        "--recursive",
        "--force",
        "--",
        "/s",
        "/q",
        "/f",
    }
)
_PROTECTED = frozenset({"input", "output", ".git", "credentials", ".env", "project.yaml"})


def literal_tokens(command: str) -> list[str]:
    # Deliberately bounded grammar: no expansion, pipelines, wrappers or compound shell.
    if re.search(r"[\n\r;|&<>`$%*?(){}]", command):
        raise ValueError("use one literal command with an exact target; expressions are unresolved")
    tokens = shlex.split(command, posix=False)
    return [
        token[1:-1] if token[:1] in {"'", '"'} and token[-1:] == token[:1] else token
        for token in tokens
    ]


def is_reparse(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def literal_cleanup_target(command: str) -> str:
    tokens = literal_tokens(command)
    if not tokens or tokens[0].casefold() not in {
        "remove-item",
        "rm",
        "rmdir",
        "rd",
        "del",
        "erase",
    }:
        raise ValueError("destructive command is wrapped or cannot be resolved")
    targets = []
    for token in tokens[1:]:
        if token.casefold() in _FLAGS:
            continue
        if token.startswith("-"):
            raise ValueError("unsupported cleanup option: " + token)
        targets.append(token)
    if len(targets) != 1 or any(c in targets[0] for c in "\"'"):
        raise ValueError("exactly one literal cleanup target is required")
    return targets[0]


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("cleanup inspection exceeded its shared 5 second budget")
    return remaining


def _contained_reference(value: str, base: Path, container: Path) -> None:
    reference = Path(value)
    if not reference.is_absolute():
        reference = base / reference
    if not reference.resolve().is_relative_to(container):
        raise ValueError("temporary container has a Git association outside its boundary")


def _git_references(path: Path, container: Path) -> None:
    """Inspect filesystem references without asking every synthetic Git repo to run."""
    if path.name == ".git" and path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir: "):
            raise ValueError("unreadable Git directory reference")
        _contained_reference(text[len("gitdir: ") :], path.parent, container)
    elif ".git" in path.relative_to(container).parts and path.is_file():
        if path.name in {"commondir", "gitdir"}:
            _contained_reference(path.read_text(encoding="utf-8").strip(), path.parent, container)
        elif path.name == "alternates" and path.parent.name == "info":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    _contained_reference(line.strip(), path.parent.parent, container)
        elif path.name == "config" and path.parent.name == ".git":
            config = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                config.read_string(path.read_text(encoding="utf-8"))
            except configparser.Error as exc:
                raise ValueError("cannot inspect Git configuration references") from exc
            if config.has_option("core", "worktree"):
                value = config.get("core", "worktree").strip()
                # Complex quoted/escaped Git config values are not proof of containment.
                if any(char in value for char in {'"', "\\"}):
                    raise ValueError("Git worktree reference cannot be resolved literally")
                _contained_reference(value, path.parent, container)


def _inspect_tree(target: Path, *, container: bool, deadline: float) -> None:
    pending = [target]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                _remaining(deadline)
                child = Path(entry.path)
                # DirEntry caches the metadata returned by the directory enumeration.
                attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                if entry.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    if not container:
                        raise ValueError("target contains a symlink or junction")
                    if not entry.is_symlink() and not child.is_junction():
                        raise ValueError("temporary container has an unknown reparse point")
                    if not child.resolve().is_relative_to(target):
                        raise ValueError("temporary container has a link outside its boundary")
                    continue  # Never traverse a link, including Windows junctions.
                if container and entry.name in {
                    ".git", "config", "commondir", "gitdir", "alternates"
                }:
                    _git_references(child, target)
                elif not container and entry.name.casefold() in _PROTECTED:
                    raise ValueError("target contains a protected asset directory")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(child)


def check_cleanup(command: str, cwd: Path, checkout: Path) -> tuple[str, str, tuple[str, ...]]:
    """Check filesystem scope only; Codex must separately establish task ownership."""
    checked_targets: tuple[str, ...] = ()
    deadline = time.monotonic() + 5
    try:
        target_text = literal_cleanup_target(command)
        raw = Path(target_text)
        checked_targets = (target_text,)
        if not raw.is_absolute():
            raw = cwd / raw
        if str(raw).startswith(("\\\\", "//")):
            raise ValueError("network/device cleanup targets are not automatically authorized")
        if any(is_reparse(p) for p in (raw, *raw.parents)):
            raise ValueError("cleanup target traverses a symlink or junction")
        target = raw.resolve()
        checkout = checkout.resolve()
        checked_targets = (str(target),)
        if checkout.is_relative_to(target):
            raise ValueError("target contains the current checkout")
        if any(part.casefold() in _PROTECTED for part in target.parts):
            raise ValueError("target traverses a protected asset directory")
        roots = [
            Path(tempfile.gettempdir()).resolve(),
            checkout / "build",
            checkout / "dist",
            checkout / ".codex-os/tmp",
        ]
        broad = {Path.home().resolve(), checkout.resolve(), *roots}
        if target in broad or target.parent == target:
            raise ValueError("broad cleanup root is forbidden")
        if not any(target != root and target.is_relative_to(root) for root in roots):
            raise ValueError("target is not a task leaf under temp, build, dist or .codex-os/tmp")
        if not target.exists():
            raise ValueError("target does not exist; its contents cannot be checked")
        repo = locate_checkout(target)
        temporary_container = (
            target.is_dir()
            and target.is_relative_to(roots[0])
            and not target.is_relative_to(checkout)
            and repo == target
            and not (target / ".git").exists()
            and not (target / ".codex-os/project.yaml").exists()
        )
        if (repo / ".git").exists():
            if repo == target:
                raise ValueError("a checkout root is not a disposable container")
            relative = target.relative_to(repo).as_posix()
            if any(
                part.casefold() in {"input", "output", ".git", "credentials"}
                for part in Path(relative).parts
            ):
                raise ValueError("target includes a protected asset directory")
            tracked = GitRunner(repo).run(
                "ls-files", "-z", "--", relative, timeout=_remaining(deadline)
            )
            if tracked.returncode != 0 or tracked.stdout:
                raise ValueError("target contains tracked files or Git inspection failed")

        _inspect_tree(target, container=temporary_container, deadline=deadline)
        _remaining(deadline)
        return (
            "CLEANUP_TARGET_CHECKED",
            "target checks passed; Codex must confirm task ownership and host approval",
            (str(target),),
        )
    except TimeoutError as exc:
        return "CLEANUP_INSPECTION_TIMEOUT", str(exc), checked_targets
    except (OSError, ValueError, RuntimeError) as exc:
        return "CLEANUP_TARGET_UNSAFE", str(exc), checked_targets
