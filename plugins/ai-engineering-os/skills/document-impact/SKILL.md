---
name: document-impact
description: Identify and update only the project documents actually affected by a change. Use at the start of any repository-changing task to plan document work, and at finish to confirm the sync. Do not bulk-rewrite documents for small changes.
---

# Document impact

Documentation is a fact source, not an essay collection. Update exactly the documents a change affects — nothing more, nothing less.

## Active documents

`README.md`, `AGENTS.md`, `docs/REQUIREMENTS.md`, `docs/SCOPE.md`, `docs/ARCHITECTURE.md`, `docs/GOVERNANCE_RULES.md`, `docs/OPEN_SOURCE_RESEARCH.md`, `docs/MEMORY.md`, `docs/WORKTREE.md`, `docs/FRONTEND_GATE.md`, `docs/TEST_PLAN.md`, `docs/CHANGELOG.md`, plus conditional `docs/API_SPEC.md` / `docs/DATABASE.md` and `docs/ADR/`.

## Rules

- Map the change to documents before coding: an API change touches API_SPEC and CHANGELOG; a schema change touches DATABASE and CHANGELOG; a governance decision needs an ADR.
- One-line backend fix must not force a rewrite of ten documents.
- `docs/memory/memory.jsonl` is runtime memory, not project documentation; it follows the memory-protocol skill.
- Update documents in the same task as the change; the Finish gate checks `docs_synced` as a task fact you declare honestly.
- `PROJECT_CONTEXT.md` is a derived cache — regenerate with `context_refresh`, never hand-edit it.
