"""Build a reviewable wheel and matching plugin; never install or publish them."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def build(root: Path, destination: Path) -> Path:
    from codex_ai_os.runtime_identity import runtime_identity

    identity = runtime_identity()
    selector = root / "src/codex_ai_os/runtime_entry.py"
    source = root / "plugins/ai-engineering-os"
    if (source / "scripts/runtime_entry.py").read_bytes() != selector.read_bytes():
        raise ValueError("plugin selector drifted from the canonical runtime source")
    controller = root / "src/codex_ai_os/process_control.py"
    if (source / "scripts/process_control.py").read_bytes() != controller.read_bytes():
        raise ValueError("plugin process controller drifted from the canonical runtime source")
    plugin_files = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in sorted(source.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    digest = hashlib.sha256(str(identity["build_fingerprint"]).encode())
    for name, content in plugin_files.items():
        digest.update(name.encode())
        digest.update(content.replace(b"\r\n", b"\n"))
    delivery_fingerprint = digest.hexdigest()
    plugin_version = str(identity["version"]) + "+codex." + delivery_fingerprint[:12]
    metadata = json.loads(plugin_files[".codex-plugin/plugin.json"])
    metadata["version"] = plugin_version
    plugin_files[".codex-plugin/plugin.json"] = json.dumps(
        metadata, ensure_ascii=False, indent=2
    ).encode("utf-8")
    plugin_files["runtime-build.json"] = json.dumps(
        identity, ensure_ascii=False, indent=2
    ).encode("utf-8")
    target = destination / ("gate-reliability-" + delivery_fingerprint[:12])
    if target.exists():
        raise FileExistsError("delivery already exists; preserve existing assets: " + str(target))
    with tempfile.TemporaryDirectory(prefix="aios-package-") as scratch:
        scratch = Path(scratch)
        subprocess.run(
            [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(scratch)],
            cwd=root,
            check=True,
        )
        wheel = next(scratch.glob("*.whl"))
        plugin = scratch / "ai-engineering-os.zip"
        with zipfile.ZipFile(plugin, "w", zipfile.ZIP_DEFLATED) as package:
            for relative, content in plugin_files.items():
                package.writestr(relative, content)
        with zipfile.ZipFile(wheel) as package:
            assert package.read("codex_ai_os/runtime_entry.py").replace(
                b"\r\n", b"\n"
            ) == selector.read_bytes().replace(b"\r\n", b"\n")
            assert package.read("codex_ai_os/process_control.py").replace(
                b"\r\n", b"\n"
            ) == controller.read_bytes().replace(b"\r\n", b"\n")
        target.mkdir(parents=True)
        shutil.copyfile(wheel, target / wheel.name)
        shutil.copyfile(plugin, target / plugin.name)
        (target / "manifest.json").write_text(
            json.dumps(
                {
                    "runtime": identity,
                    "delivery_fingerprint": delivery_fingerprint,
                    "plugin_version": plugin_version,
                    "plugin_files": {
                        name: hashlib.sha256(content).hexdigest()
                        for name, content in plugin_files.items()
                    },
                    "sha256": {
                        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (wheel, plugin)
                    },
                    "artifacts": [wheel.name, plugin.name],
                    "installation": "not_performed",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return target


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "output")
    args = parser.parse_args()
    print(build(root, args.output.resolve()))


if __name__ == "__main__":
    main()
