---
name: finish-checklist
description: Close a governed task through the Finish gate — targeted tests, document sync, repository hygiene, disposable cleanup, and memory. Use at the end of every repository-changing task in a governed project before reporting completion.
---

# Finish checklist

Run these in order, then call `governance_check(stage="finish", tests_passed=true, docs_synced=true, memory_written=true)` or declare `memory_not_needed=true` (CLI: `codex-os finish ...`). The gate re-verifies the repository side itself.

1. **Targeted tests** — run the narrowest tests covering the changed requirements; they must pass. No full-suite re-runs of unrelated subsets.
2. **Document sync** — every document affected by this change is updated (document-impact skill).
3. **Repository hygiene** — no copy-style directories or files, no tracked pollution, no unresolved conflicts (the gate checks).
4. **Disposable cleanup** — delete temp scripts, caches, debug files, and one-off artifacts; never commit them. Promote to `scripts/` only if genuinely reusable.
5. **Memory** — record durable lessons per the memory-protocol skill, or declare `memory_not_needed` honestly when nothing was learned.
6. **Git** — one Conventional Commit for the logical change, pushed to the task branch; `git status --porcelain` clean afterwards.

A blocked gate is a report to the user, not an obstacle to route around.
