# ADR-0017 — Preserve existing native repositories

Date:2026-10-03. Status:accepted.

Initialization must preserve the contents of existing input/output directories.
Directory presence satisfies their baseline requirement; new empty projects may
still receive markers. Native applications can therefore require exactly their
deployed files without disabling document checks.

CMake compiler-detection Debug/tmp folders and a vendor's copy.cpp are not source
history copies. Prove CMakeFiles using both matching CMake home/cache directories;
leave the rest of a build tree checked. Prove declared vendors using exact origin,
commit and permitted patched-file hashes in .codex-os/source-provenance.json.
Copied configured first-party source inside either tree remains blocking.

Tests include missing/incorrect CMake evidence, modified vendor files and disguised
first-party copies. Gitignore alone never provides an exemption. User asset
directories and unrelated dirty checkouts remain untouched.
