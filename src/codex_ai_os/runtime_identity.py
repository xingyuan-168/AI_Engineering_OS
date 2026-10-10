"""Identity captured once at process boot; never refreshed from updated disk."""

import hashlib
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _capture() -> dict[str, object]:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in {".py", ".sql", ".json", ".yaml"}
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    try:
        package_version = version("codex-ai-engineering-os")
    except PackageNotFoundError:
        package_version = "1.0.0"
    return {
        "product": "codex-ai-engineering-os",
        "version": package_version,
        "api_version": "2.0",
        "config_versions": ["1.0", "1.1", "1.2"],
        "module_path": str(root),
        "build_fingerprint": digest.hexdigest(),
    }


BOOT_IDENTITY = _capture()


def runtime_identity() -> dict[str, object]:
    return deepcopy(BOOT_IDENTITY)
