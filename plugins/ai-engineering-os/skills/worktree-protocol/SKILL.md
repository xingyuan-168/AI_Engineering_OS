---
name: worktree-protocol
description: Isolate parallel or risky task work in a disposable Git worktree under .worktrees/ with Codex-native subagents. Use when tasks would write overlapping paths in parallel, or when a long experiment should not touch the main worktree. Not needed for ordinary sequential work.
---

# Worktree protocol

Codex stays in charge of deciding when to use subagents and how to split work. AIOS only keeps parallel writes isolated and registered.

## Prepare

- Create with `worktree_manage(action="prepare", name="task-slug")` (or `codex-os worktree prepare`). Worktrees live under `.worktrees/` and are registered so the hook treats them as disposable.
- Each parallel subagent works in its own worktree; subagents never write outside their worktree.

## During

- Destructive-but-local commands (reset --hard, clean -f, branch -D, recursive deletes) are allowed inside your disposable worktree and denied in the main worktree.
- Subagents do not write `docs/memory/`; submit candidates with `codex-os memory record --candidate` and let the main session merge at finish.

## Finish and cleanup

1. Commit all work in the worktree.
2. `worktree_manage(action="finish", name=...)` — it refuses a dirty worktree so subagent work is never lost.
3. The main session reviews, merges, and then runs `worktree_manage(action="cleanup", name=...)`, which removes the worktree, deletes its branch, and unregisters it.
4. No orphan worktrees: after merge the worktree goes away; the name, path, and branch become reusable.
