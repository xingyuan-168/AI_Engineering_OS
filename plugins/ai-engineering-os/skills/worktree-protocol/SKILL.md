---
name: worktree-protocol
description: Isolate parallel or risky task work in a disposable Git worktree under .worktrees/ with Codex-native subagents. Use when tasks would write overlapping paths in parallel, or when a long experiment should not touch the main worktree. Not needed for ordinary sequential work.
---

# Worktree protocol

Codex stays in charge of deciding when to use subagents and how to split work. AIOS only keeps parallel writes isolated and registered.

## Prepare

- Create with the coordinator project root:

```python
worktree_manage(project_root="<project-root>", action="prepare", name="task-slug")
```

Worktrees live under `.worktrees/`. Both the runtime registration and Git's real worktree list must match; nested calls resolve to the registered checkout.
- Each parallel subagent works in its own worktree; subagents never write outside their worktree.

## During

- Local Git discard commands are allowed only in the registered disposable checkout; force push and shared-ref deletion remain forbidden. File cleanup is judged by its actual target, never merely by cwd. The whole checkout and tracked files are not automatic cleanup targets.
- Subagents do not write `docs/memory/`; submit candidates with `codex-os memory record --candidate` and let the main session merge at finish.

## Finish and cleanup

1. Commit all work in the worktree.
2. `worktree_manage(action="finish", name=...)` — it refuses a dirty worktree so subagent work is never lost.
3. The main session reviews, merges, and calls `worktree_manage` with `project_root`, `action="cleanup"` and `name`. It verifies path, registration, clean state and merge ancestry, then uses non-force worktree removal and `git branch -d`. Partial failure retains recovery records; never escalate to force.
4. No orphan worktrees: after merge the worktree goes away; the name, path, and branch become reusable.
