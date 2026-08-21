"""Path-safe atomic document creation and governance checks."""

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
FORBIDDEN_COPY_NAMES = {"backup", "copy", "final", "new", "v1", "v2"}


class PathDeniedError(PermissionError):
    """Raised when a requested path escapes the project root."""


@dataclass(frozen=True, slots=True)
class DocumentCheckReport:
    ok: bool
    checked_files: int
    missing: tuple[str, ...]
    broken_links: tuple[str, ...]
    invalid_documents: tuple[str, ...]
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
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True

    def initialize_documents(self, project_name: str, project_type: str) -> tuple[str, ...]:
        created: list[str] = []
        for relative, template in documents_for(project_type).items():
            content = template.replace("{{ project_name }}", project_name)
            if self.write_atomic(relative, content, overwrite=False):
                created.append(relative)
        return tuple(created)

    def generate_context(self) -> Path:
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

    def check(self, project_type: str) -> DocumentCheckReport:
        expected = documents_for(project_type)
        missing = tuple(sorted(path for path in expected if not self.resolve(path).is_file()))
        invalid: list[str] = []
        broken: list[str] = []
        checked = 0

        docs_root = self.resolve("docs")
        for path in sorted(docs_root.rglob("*.md")) if docs_root.is_dir() else []:
            checked += 1
            text = path.read_text(encoding="utf-8")
            if not text.lstrip().startswith("# "):
                invalid.append(path.relative_to(self.project_root).as_posix())
            broken.extend(self._broken_links(path, text))

        forbidden = tuple(
            sorted(
                path.relative_to(self.project_root).as_posix()
                for path in self.project_root.rglob("*")
                if path.is_dir()
                and path.name.casefold() in FORBIDDEN_COPY_NAMES
                and ".git" not in path.parts
            )
        )
        return DocumentCheckReport(
            ok=not missing and not broken and not invalid and not forbidden,
            checked_files=checked,
            missing=missing,
            broken_links=tuple(sorted(set(broken))),
            invalid_documents=tuple(sorted(invalid)),
            forbidden_directories=forbidden,
        )

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
