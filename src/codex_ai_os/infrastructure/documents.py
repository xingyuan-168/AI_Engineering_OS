"""Path-safe atomic document creation and lightweight doc checks (ADR-0016).

The document layer owns exactly three duties: create the minimal project
document set, regenerate the derived project context, and verify that the
docs/ tree is present, link-clean, and free of copy-style directories.
Heavy governance metadata, traceability, and staleness machinery were
removed with ADR-0016; affected-document syncing stays a Codex process
discipline checked by the Finish gate.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from codex_ai_os.templates.project_docs import documents_for

LOCAL_LINK = re.compile(r"\[[^\]]+\]\((?:<([^>]+)>|([^ )]+))")
FORBIDDEN_COPY_NAMES = frozenset(
    {"backup", "copy", "debug", "final", "new", "old", "src_backup", "temp", "tmp"}
)
EXCLUDED_GOVERNANCE_TREES = frozenset(
    {
        ".codex",
        ".codex-os",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".worktrees",
        "node_modules",
    }
)


class PathDeniedError(PermissionError):
    """Raised when a requested path escapes the project root."""


@dataclass(frozen=True, slots=True)
class DocumentCheckReport:
    ok: bool
    checked_files: int
    missing: tuple[str, ...]
    broken_links: tuple[str, ...]
    forbidden_directories: tuple[str, ...]


class DocumentManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def resolve(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise PathDeniedError("absolute output paths are not allowed")
        target = (self.project_root / relative).resolve()
        if not target.is_relative_to(self.project_root):
            raise PathDeniedError(f"path escapes project root: {relative_path}")
        return target

    def write_atomic(self, relative_path: str | Path, content: str, *, overwrite: bool) -> bool:
        target = self.resolve(relative_path)
        if target.exists() and not overwrite:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True

    def initialize_documents(
        self,
        project_name: str,
        project_type: str,
        *,
        include: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[str, ...]:
        """Create the baseline document set plus explicitly requested extras."""

        created: list[str] = []
        for relative, template in documents_for(project_type, include=include).items():
            content = template.replace("{{ project_name }}", project_name)
            if self.write_atomic(relative, content, overwrite=False):
                created.append(relative)
        return tuple(created)

    def generate_context(self) -> Path:
        """Regenerate the derived PROJECT_CONTEXT.md cache."""

        docs_root = self.resolve("docs")
        lines = [
            "# Generated Project Context",
            "",
            "> Derived cache. Source documents and Git history remain authoritative.",
            "",
            "## Source hashes",
            "",
        ]
        if docs_root.is_dir():
            for path in sorted(docs_root.rglob("*.md")):
                relative = path.relative_to(self.project_root).as_posix()
                lines.append(f"- `{relative}`: `{sha256_file(path)}`")
        context_path = self.resolve(".codex-os/context/PROJECT_CONTEXT.md")
        self.write_atomic(
            context_path.relative_to(self.project_root),
            "\n".join(lines) + "\n",
            overwrite=True,
        )
        return context_path

    def check(
        self,
        *,
        include: frozenset[str] | set[str] = frozenset(),
    ) -> DocumentCheckReport:
        """Verify presence, links, and copy-free hygiene of the docs/ tree."""

        expected = documents_for("generic", include=include)
        missing = tuple(
            sorted(path for path in expected if not self.resolve(path).is_file())
        )
        broken: list[str] = []
        checked = 0
        docs_root = self.resolve("docs")
        for path in sorted(docs_root.rglob("*.md")) if docs_root.is_dir() else []:
            checked += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                broken.append(
                    path.relative_to(self.project_root).as_posix() + " -> unreadable"
                )
                continue
            broken.extend(self._broken_links(path, text))
        return DocumentCheckReport(
            ok=not (missing or broken or self._forbidden_directories()),
            checked_files=checked,
            missing=missing,
            broken_links=tuple(sorted(set(broken))),
            forbidden_directories=self._forbidden_directories(),
        )

    def _forbidden_directories(self) -> tuple[str, ...]:
        docs_root = self.resolve("docs")
        if not docs_root.is_dir():
            return ()
        forbidden: list[str] = []
        for path in sorted(docs_root.rglob("*")):
            if not path.is_dir():
                continue
            relative = path.relative_to(self.project_root)
            if any(part.casefold() in EXCLUDED_GOVERNANCE_TREES for part in relative.parts):
                continue
            if path.name.casefold() in FORBIDDEN_COPY_NAMES:
                forbidden.append(relative.as_posix())
        return tuple(forbidden)

    def _broken_links(self, source: Path, text: str) -> list[str]:
        broken: list[str] = []
        for match in LOCAL_LINK.finditer(text):
            raw = match.group(1) or match.group(2)
            if raw.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_text = unquote(raw.split("#", 1)[0])
            if not target_text:
                continue
            target = (source.parent / target_text).resolve()
            if not target.is_relative_to(self.project_root) or not target.exists():
                source_name = source.relative_to(self.project_root).as_posix()
                broken.append(f"{source_name} -> {raw}")
        return broken


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DocumentCheckReport",
    "DocumentManager",
    "PathDeniedError",
    "sha256_file",
]
