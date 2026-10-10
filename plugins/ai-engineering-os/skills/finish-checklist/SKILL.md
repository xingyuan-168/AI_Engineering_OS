---
name: finish-checklist
description: Close a governed task through the Finish gate — targeted tests, document sync, repository hygiene, disposable cleanup, and memory. Use at the end of every repository-changing task in a governed project before reporting completion.
---

# Finish checklist

Run these in order, then use the task's captured starting commit. Missing `base_ref` blocks with `FINISH_BASE_REQUIRED`; never substitute the current HEAD after making commits.

```python
governance_check(project_root="<project-root>", stage="finish", base_ref="<task-start-sha>", change_class="bugfix", test_command="pytest", memory_not_needed=True)
```

CLI: `codex-os finish <project-root> --base-ref <task-start-sha> --change-class bugfix --test-command "pytest" --memory-not-needed`. Use `memory_written=True` instead when durable memory was recorded. Supply `requirement_id` for research-required changes. Checks cover committed, staged, unstaged and untracked paths.

Provide `remote` / `--remote` when the task selects a push target. Supply a UUID `run_id` / `--run-id` before long checks; after cancellation or disconnect use `governance_check(project_root="<project-root>", stage="finish", action="status", run_id="<UUID>")` or `codex-os finish <project-root> --status --run-id <UUID> --json`. Status accepts no execution parameters. Only completed with decision.allowed=true passes; partial/skipped checks never mean full success.

1. **Targeted tests** — run the narrowest tests covering the changed requirements; they must pass. No full-suite re-runs of unrelated subsets.
2. **Document sync** — every document affected by this change is updated (document-impact skill).
3. **Repository hygiene** — no copy-style directories or files, no tracked pollution, no unresolved conflicts (the gate checks).
4. **Disposable cleanup** — establish task ownership and user authority in native context, then check each exact target. AIOS checks paths, tracked files and links; untracked does not mean disposable. Never change cwd, shell or language to bypass a host refusal. Use `authorize-hook --explain` only for AIOS diagnostics.
5. **Memory** — record durable lessons per the memory-protocol skill, or declare `memory_not_needed` honestly when nothing was learned.
6. **Git** — Conventional Commits for logical changes, normally pushed to the task branch; preserve unrelated user modifications. Missing tools/tests are skipped or blocked, not passed.

A blocked gate is a report to the user, not an obstacle to route around.

Finish checks actual Git ignore and tracking rules before creating diagnostics; fix reported configuration explicitly if blocked. Queries never create locks or change records. A released invocation lock without a terminal result means interrupted, even while the MCP process lives; legacy running records without invocation evidence are unverifiable. Neither is a passed Gate.
