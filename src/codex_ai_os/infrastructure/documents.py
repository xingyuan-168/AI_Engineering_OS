"""Path-safe atomic document creation and governance checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import unquote

from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.templates.project_docs import documents_for

LOCAL_LINK = re.compile(r"\[[^\]]+\]\((?:<([^>]+)>|([^ )]+))")
FORBIDDEN_COPY_NAMES = {
    "backup",
    "copy",
    "debug",
    "final",
    "new",
    "old",
    "src_backup",
    "temp",
    "tmp",
}
EXCLUDED_GOVERNANCE_TREES = {
    ".codex-os",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "node_modules",
}
DOCUMENT_METADATA = re.compile(r"<!--\s*codex-os-document:\s*(\{.*?\})\s*-->")
UNAPPROVED_PLACEHOLDER = re.compile(r"(?i)(?:\bTODO\b|\bTBD\b|待确认|待补充)")
STRICT_STATUSES = {"accepted", "approved", "released"}
REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "PRODUCT_REQUIREMENTS.md": ("范围", "成功", "验收"),
    "ARCHITECTURE.md": ("定位", "组件", "信任", "恢复"),
    "API_SPEC.md": ("接口", "错误", "兼容"),
    "DATABASE.md": ("迁移", "事务", "恢复"),
    "SECURITY.md": ("信任", "威胁", "风险"),
}


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
    metadata_errors: tuple[str, ...] = ()
    placeholder_findings: tuple[str, ...] = ()
    version_mismatches: tuple[str, ...] = ()
    stale_documents: tuple[str, ...] = ()
    impact_findings: tuple[str, ...] = ()


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
        metadata_errors: list[str] = []
        placeholders: list[str] = []
        version_mismatches: list[str] = []
        stale: list[str] = []
        impact: list[str] = []
        checked = 0

        docs_root = self.resolve("docs")
        for path in sorted(docs_root.rglob("*.md")) if docs_root.is_dir() else []:
            checked += 1
            text = path.read_text(encoding="utf-8")
            if not text.lstrip().startswith("# "):
                invalid.append(path.relative_to(self.project_root).as_posix())
            broken.extend(self._broken_links(path, text))
            relative = path.relative_to(self.project_root).as_posix()
            metadata = self._metadata(relative, text, metadata_errors)
            if metadata is not None:
                status = str(metadata.get("status", "")).casefold()
                if status in STRICT_STATUSES and UNAPPROVED_PLACEHOLDER.search(text):
                    placeholders.append(relative)
                document_version = str(metadata.get("document_version", ""))
                schema_version = str(metadata.get("schema_version", ""))
                if status in STRICT_STATUSES and (
                    document_version != RUNTIME_VERSIONS.software
                    or schema_version != RUNTIME_VERSIONS.document_schema
                ):
                    version_mismatches.append(relative)
                expires_at = metadata.get("expires_at")
                if isinstance(expires_at, str) and _is_expired(expires_at):
                    stale.append(relative)
                if status in STRICT_STATUSES:
                    for required in REQUIRED_SECTIONS.get(path.name, ()):
                        if not any(
                            required.casefold() in heading.casefold()
                            for heading in re.findall(r"^#{2,6}\s+(.+)$", text, re.MULTILINE)
                        ):
                            impact.append(f"{relative}: missing section containing {required}")

        forbidden: list[str] = []
        for path in self.project_root.rglob("*"):
            if not path.is_dir() or not (
                path.name.casefold() in FORBIDDEN_COPY_NAMES
                or re.fullmatch(r"v\d+", path.name.casefold())
            ):
                continue
            relative = path.relative_to(self.project_root)
            if any(part.casefold() in EXCLUDED_GOVERNANCE_TREES for part in relative.parts):
                continue
            forbidden.append(relative.as_posix())
        return DocumentCheckReport(
            ok=not any(
                (
                    missing,
                    broken,
                    invalid,
                    forbidden,
                    metadata_errors,
                    placeholders,
                    version_mismatches,
                    stale,
                    impact,
                )
            ),
            checked_files=checked,
            missing=missing,
            broken_links=tuple(sorted(set(broken))),
            invalid_documents=tuple(sorted(invalid)),
            forbidden_directories=tuple(sorted(forbidden)),
            metadata_errors=tuple(sorted(metadata_errors)),
            placeholder_findings=tuple(sorted(placeholders)),
            version_mismatches=tuple(sorted(version_mismatches)),
            stale_documents=tuple(sorted(stale)),
            impact_findings=tuple(sorted(impact)),
        )

    @staticmethod
    def _metadata(
        relative: str, text: str, errors: list[str]
    ) -> dict[str, object] | None:
        match = DOCUMENT_METADATA.search(text)
        if match is None:
            return None
        try:
            raw: object = json.loads(match.group(1))
        except json.JSONDecodeError:
            errors.append(f"{relative}: invalid metadata JSON")
            return None
        if not isinstance(raw, dict):
            errors.append(f"{relative}: metadata must be an object")
            return None
        object_mapping = cast(dict[object, object], raw)
        metadata: dict[str, object] = {
            str(key): value for key, value in object_mapping.items()
        }
        required = {
            "schema_version",
            "document_version",
            "status",
            "owner",
            "requirement_refs",
        }
        missing = sorted(required - set(metadata))
        if missing:
            errors.append(f"{relative}: missing metadata fields {missing}")
        schema_version = metadata.get("schema_version")
        if schema_version not in RUNTIME_VERSIONS.compatible_document_schemas:
            errors.append(
                f"{relative}: unsupported schema_version {schema_version!r}; expected one of "
                f"{list(RUNTIME_VERSIONS.compatible_document_schemas)}"
            )
        refs = metadata.get("requirement_refs")
        if isinstance(refs, list):
            ref_objects = cast(list[object], refs)
            valid_refs = bool(ref_objects) and all(
                isinstance(item, str) for item in ref_objects
            )
        else:
            valid_refs = False
        if not valid_refs:
            errors.append(f"{relative}: requirement_refs must be a non-empty string array")
        if not isinstance(metadata.get("owner"), str) or not str(metadata.get("owner")).strip():
            errors.append(f"{relative}: owner is required")
        return metadata

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


def _is_expired(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed < datetime.now(UTC)
